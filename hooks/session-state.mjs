#!/usr/bin/env node
// Состояние сессии для хуков набора — перенос hooks/session_state.py на Node с тем же поведением
// (Python-версия — эталон и запасной путь без Node). Описание — DOC ниже, его же печатает --help;
// устройство хуков — hooks/README.md.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DOC = `Состояние сессии для хуков набора: модель, effort, ultracode, контекст, лимиты, дневная норма.

Один модуль на три хука (context-guard, model-advisor, limit-guard). Каждое поле — из самого
свежего источника, с пометкой, откуда оно взято (\`src\`). Источники:
  мод session-state  — <claude>/session-status/<sid>.state.json (обе оболочки, mods/session-state);
  статусная строка   — <claude>/session-status/<sid>.json, rate-limits.json, week-budget.json
                       (только терминал, terminal/bin/claude-statusline);
  транскрипт сессии  — transcript_path из входа хука (JSONL, читается с конца).
<claude> — CLAUDE_CONFIG_DIR или ~/.claude.

Части считаются лениво: session_state(inp, {fields}) трогает только нужные источники — хуки
висят на каждом PreToolUse/PostToolUse с таймаутом 10 с. Функции не бросают исключений: чего
нет — null.

Здесь же общий ввод-вывод хуков: read_input, emit_context, emit_json, write_json (атомарно),
block_stderr, dump_input. Перенос session_state.py: Node 18+, только встроенные модули, без внешних
команд; поведение и файлы — как у Python-версии.

Отладка: node hooks/session-state.mjs <session_id> [transcript.jsonl] — результат JSON с src.
`;

export const FIELDS_ALL = Object.freeze(["model", "effort", "ultracode", "context", "limits", "day"]);
// Порог автосжатия без мода: min(autoCompactWindow или окно, окно) − резерв (20k вывод + 13k буфер).
// Сходится с замерами: окно 1M → 967k (preTokens границы сжатия), autoCompactWindow 500k → 467k.
// Для окон < 1M (200k → 167k) не замерено.
export const AUTOCOMPACT_RESERVE = 33_000;
// Показания ближе этого (с) — ничья, и тогда мод > статусная строка > транскрипт.
export const TIE = 2.0;
export const PRIORITY = Object.freeze({ mod: 3, statusline: 2, transcript: 1 });
export const LIMITS_MAX_AGE = 8 * 86400; // файлы мода старше — в лимитах не участвуют
export const STATUS_TTL_DAYS = 30;
export const DAY = 86400;
export const WEEK = 7 * DAY;
export const TAIL_MAX = 64 << 20; // транскрипт с конца читается не дальше
export const ULTRA_CHUNK = 8 << 20;
export const WINDOWS = Object.freeze(["five_hour", "seven_day"]);
export const CONTEXT_KEYS = Object.freeze(["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]);
// \s и \d в str-шаблонах re Python — юникодные и не те, что у RegExp (у него \d — только ASCII,
// \s без \x1c-\x1f и \x85, зато с ﻿): классы выписаны явно. \d — \p{Nd} (Unicode версии Node).
export const PY_S = "\\t-\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
export const NATIVE_1M = /fable|sonnet-(?:[5-9]|\p{Nd}\p{Nd})|opus-(?:4-[7-9]|[5-9]|\p{Nd}\p{Nd})|\[1m\]/iu;
// Ultracode — последнее из ответа команды /effort («Ultracode on/off») и вложений
// ultra_effort_enter / ultra_effort_exit; то же правило, что в claude-statusline.
// Шаблон байтовый, как в Python: применяется к строке latin1 (байт = символ), \s — пробелы ASCII.
export const ULTRA_EVENT = /"content":[ \t\n\r\f\v]*"<local-command-stdout>[^"]{0,40}Ultracode (on|off)|"type":[ \t\n\r\f\v]*"ultra_effort_(enter|exit)"/g;
export const MODEL_IN_TEXT = new RegExp(`exact model ID is ([^${PY_S}]+?)\\.?(?:[${PY_S}]|$)`, "u");
// Python fullmatch — ^…$ без флага m.
export const TOKENS_RE = new RegExp(`^[${PY_S}]*(\\p{Nd}+(?:[.,]\\p{Nd}+)?)[${PY_S}]*([kKкКmMмМ]?)[${PY_S}]*$`, "u");
export const UNITS = Object.freeze({ k: 1_000, "к": 1_000, m: 1_000_000, "м": 1_000_000 });

// --- Python-семантика ---------------------------------------------------------------------------
// Истинность, str(), round(), int(), //, json.dumps — как в Python: от них зависят ветки и файлы.
// Экспортированные _-функции берут и другие хуки на Node (context-guard.mjs).

const UTF8 = new TextDecoder("utf-8", { fatal: true }); // BOM в начале снимает, как utf-8-sig

export function _is_dict(x) {
  return x !== null && typeof x === "object" && !Array.isArray(x);
}

export function _get(obj, key) {
  return _is_dict(obj) && Object.hasOwn(obj, key) ? obj[key] : null;
}

export function _truthy(x) {
  if (x === null || x === undefined) return false;
  if (typeof x === "object") return Array.isArray(x) ? x.length > 0 : Object.keys(x).length > 0;
  return Boolean(x) || Number.isNaN(x); // nan в Python истинно
}

export function _or(...values) {
  for (const v of values) if (_truthy(v)) return v;
  return values[values.length - 1];
}

export function _well_formed(s) {
  return !/[\uD800-\uDFFF]/u.test(s); // одиночный суррогат Python в UTF-8 не кодирует
}

export function _py_int(x) {
  // int(x) Python для числа и bool (int(True) == 1); строки — k() в context-guard.mjs.
  if (typeof x === "boolean") return Number(x);
  if (!Number.isFinite(x)) throw new RangeError("cannot convert float to integer");
  return Math.trunc(x) || 0;
}

function _py_round(x) {
  // round(x) Python: до целого, половина — к чётному.
  if (!Number.isFinite(x)) throw new RangeError("cannot convert float to integer");
  const f = Math.floor(x);
  const d = x - f;
  if (d !== 0.5) return d > 0.5 ? f + 1 : f;
  return f % 2 === 0 ? f : f + 1;
}

export function _floordiv(a, b) {
  // a // b Python (float_floor_div: через fmod, а не floor(a / b)).
  let mod = a % b;
  let div = (a - mod) / b;
  if (mod && (b < 0) !== (mod < 0)) div -= 1;
  if (!div) return 0;
  let floor = Math.floor(div);
  if (div - floor > 0.5) floor += 1;
  return floor;
}

function _py_num(x, inf = "Infinity", nan = "NaN") {
  // repr числа Python. Целое и целое float после JSON.parse не различить — пишется как int.
  if (Number.isNaN(x)) return nan;
  if (!Number.isFinite(x)) return x > 0 ? inf : `-${inf}`;
  if (Number.isInteger(x)) return Number.isSafeInteger(x) ? String(x || 0) : BigInt(x).toString();
  const e = x.toExponential(); // кратчайшие цифры, как repr
  if (Number(e.slice(e.indexOf("e") + 1)) >= -4) return String(x);
  return e.replace(/e([+-])(\d)$/, (_, sign, digit) => `e${sign}0${digit}`);
}

const PY_UNPRINTABLE = /^[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]$/u;

export function _py_str_repr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  const hex = (c, n) => c.toString(16).padStart(n, "0");
  let out = q;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (ch === q || ch === "\\") out += `\\${ch}`;
    else if (ch === "\t") out += "\\t";
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (c < 0x20 || c === 0x7f) out += `\\x${hex(c, 2)}`;
    else if (c < 0x7f || !PY_UNPRINTABLE.test(ch)) out += ch;
    else out += c <= 0xff ? `\\x${hex(c, 2)}` : c <= 0xffff ? `\\u${hex(c, 4)}` : `\\U${hex(c, 8)}`;
  }
  return out + q;
}

function _py_repr(x) {
  if (x === null || x === undefined) return "None";
  if (x === true) return "True";
  if (x === false) return "False";
  if (typeof x === "number") return _py_num(x, "inf", "nan");
  if (typeof x === "string") return _py_str_repr(x);
  if (Array.isArray(x)) return `[${x.map(_py_repr).join(", ")}]`;
  if (_is_dict(x)) return `{${Object.entries(x).map(([k, v]) => `${_py_str_repr(k)}: ${_py_repr(v)}`).join(", ")}}`;
  return String(x);
}

export function _py_str(x) {
  return typeof x === "string" ? x : _py_repr(x);
}

const JSON_ESC = { '"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f" };

function _json_str(s, ensure_ascii) {
  const re = ensure_ascii ? /["\\\x00-\x1f\x7f-￿]/g : /["\\\x00-\x1f]/g;
  return `"${s.replace(re, (c) => JSON_ESC[c] || `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`)}"`;
}

export function dumps(obj, { ensure_ascii = true, indent = null, separators = null } = {}) {
  // json.dumps Python: те же разделители, экранирование и запись чисел.
  const [item_sep, key_sep] = separators || (indent === null ? [", ", ": "] : [",", ": "]);
  const unit = typeof indent === "number" ? " ".repeat(indent) : indent;
  const stack = new Set();
  const enc = (x, level) => {
    if (x === null || x === undefined) return "null";
    if (x === true) return "true";
    if (x === false) return "false";
    if (typeof x === "string") return _json_str(x, ensure_ascii);
    if (typeof x === "number") return _py_num(x);
    const plain = _is_dict(x) && [Object.prototype, null].includes(Object.getPrototypeOf(x));
    if (!Array.isArray(x) && !plain) throw new TypeError(`Object of type ${typeof x} is not JSON serializable`);
    if (stack.has(x)) throw new TypeError("Circular reference detected");
    const items = Array.isArray(x) ? x.map((v) => [null, v]) : Object.entries(x).filter(([, v]) => v !== undefined);
    const [open, close] = Array.isArray(x) ? ["[", "]"] : ["{", "}"];
    if (!items.length) return open + close;
    stack.add(x);
    const pad = unit === null ? "" : `\n${unit.repeat(level + 1)}`;
    const body = items.map(([k, v]) => (k === null ? "" : _json_str(k, ensure_ascii) + key_sep) + enc(v, level + 1));
    stack.delete(x);
    return open + pad + body.join(item_sep + pad) + (unit === null ? "" : `\n${unit.repeat(level)}`) + close;
  };
  return enc(obj, 0);
}

export function _sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

export function _write_all(fd, buf) {
  // Синхронно и целиком: после process.exit асинхронный вывод теряется.
  let off = 0;
  while (off < buf.length) {
    try {
      off += fs.writeSync(fd, buf, off, buf.length - off);
    } catch (e) {
      if (e.code !== "EAGAIN") throw e;
      _sleep(5);
    }
  }
}

function _print(text) {
  // print() Python: перевод строки в конце, на Windows "\n" в канале становится "\r\n".
  const out = `${text}\n`;
  _write_all(1, Buffer.from(process.platform === "win32" ? out.replace(/\n/g, "\r\n") : out, "utf8"));
}

export function _time() {
  return Date.now() / 1000;
}

function _env(name) {
  const v = process.env[name];
  return v === undefined ? null : v;
}

// --- пути --------------------------------------------------------------------------------------

function _norm(p) {
  // Path(p) Python: без "//", "." и хвостового разделителя ("..", в отличие от pathlib, схлопывается).
  const n = path.normalize(p);
  const root = path.parse(n).root;
  return n.length > root.length ? n.replace(process.platform === "win32" ? /[\\/]+$/ : /\/+$/, "") : n;
}

export function _home() {
  // Path.home() Python 3.11: HOME (POSIX) или USERPROFILE / HOMEDRIVE+HOMEPATH (Windows), иначе
  // учётная запись; не определить — «~», как запасной os.path.expanduser.
  const env = process.env;
  if (process.platform === "win32") {
    if (env.USERPROFILE !== undefined) return env.USERPROFILE;
    if (env.HOMEPATH !== undefined) return path.join(env.HOMEDRIVE || "", env.HOMEPATH);
    return "~";
  }
  if (env.HOME !== undefined) return env.HOME.replace(/\/+$/, "") || "/";
  try {
    return os.userInfo().homedir.replace(/\/+$/, "") || "/";
  } catch {
    return "~";
  }
}

export function claude_dir() {
  // Каталог Claude Code: CLAUDE_CONFIG_DIR или ~/.claude (на Windows домашний — USERPROFILE).
  const env = process.env.CLAUDE_CONFIG_DIR;
  return env ? _norm(env) : path.join(_home(), ".claude");
}

export function status_dir() {
  return path.join(claude_dir(), "session-status");
}

export function safe_sid(sid) {
  // session_id для имени файла — как в моде session-state (для UUID совпадает с сырым).
  return _py_str(_or(sid, "unknown")).replace(/[^A-Za-z0-9_-]/gu, "_");
}

function _listdir(dir) {
  // Порядок каталога, как os.scandir (readdirSync сортирует имена).
  const d = fs.opendirSync(dir);
  const names = [];
  try {
    for (let e = d.readSync(); e !== null; e = d.readSync()) names.push(e.name);
  } finally {
    d.closeSync();
  }
  return names;
}

const GLOB_CACHE = new Map();

function _glob(dir, pattern) {
  // Path.glob с одним «*» на уровне каталога: точечные имена тоже, нет каталога — пусто.
  let re = GLOB_CACHE.get(pattern);
  if (!re) {
    const body = pattern.split("*").map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s\\S]*");
    re = new RegExp(`^${body}$`, process.platform === "win32" ? "iu" : "u");
    GLOB_CACHE.set(pattern, re);
  }
  let names;
  try {
    names = _listdir(dir);
  } catch {
    return [];
  }
  return names.filter((n) => re.test(n)).map((n) => path.join(dir, n));
}

export function _mtime(p) {
  // st_mtime Python: sec + nsec * 1e-9 — те же биты, что у Python, а не mtimeMs / 1000.
  const ns = fs.statSync(p, { bigint: true }).mtimeNs;
  let sec = ns / 1_000_000_000n;
  let nsec = ns - sec * 1_000_000_000n;
  if (nsec < 0n) {
    sec -= 1n;
    nsec += 1_000_000_000n;
  }
  return Number(sec) + Number(nsec) * 1e-9;
}

// --- ввод-вывод хуков --------------------------------------------------------------------------

function _read_stdin() {
  const chunks = [];
  const buf = Buffer.alloc(1 << 16);
  for (;;) {
    let n;
    try {
      n = fs.readSync(0, buf, 0, buf.length, null);
    } catch (e) {
      if (e.code === "EAGAIN") {
        _sleep(10);
        continue;
      }
      if (e.code === "EOF") break; // Windows: канал закрыт
      throw e;
    }
    if (!n) break;
    chunks.push(Buffer.from(buf.subarray(0, n)));
  }
  return Buffer.concat(chunks);
}

export function read_input(dump_env = null) {
  // Вход хука из stdin: объект или null (пусто, мусор, не объект). dump_env — имя переменной
  // с файлом для отладочной записи сырого входа (CONTEXT_GUARD_DUMP, LIMIT_GUARD_DUMP).
  let raw;
  try {
    raw = _read_stdin();
  } catch {
    return null;
  }
  if (dump_env) dump_input(raw, dump_env);
  let inp;
  try {
    inp = JSON.parse(UTF8.decode(raw));
  } catch {
    return null;
  }
  return _is_dict(inp) ? inp : null;
}

export function dump_input(raw, env_name) {
  // Дописать сырой вход хука в файл из переменной env_name; переменной нет — ничего.
  const p = _env(env_name);
  if (!p) return;
  const data = Buffer.isBuffer(raw) ? raw : Buffer.from(_py_str(raw), "utf8");
  try {
    fs.appendFileSync(p, Buffer.concat([data, Buffer.from("\n")]));
  } catch {
    // как OSError в Python — молча
  }
}

export function emit_json(obj) {
  // JSON в stdout только ASCII: на Windows кириллица иначе бьётся.
  const text = dumps(obj, { ensure_ascii: true });
  try {
    _print(text);
  } catch {
    // stdout закрыт (EPIPE) — хуку падать нельзя
  }
}

export function emit_context(event, text) {
  emit_json({ hookSpecificOutput: { hookEventName: event, additionalContext: text } });
}

export function block_stderr(text) {
  // Блок с причиной в stderr (байтами UTF-8) и код 2.
  try {
    if (typeof text === "string" && _well_formed(text)) _write_all(2, Buffer.from(text, "utf8"));
  } catch {
    // как в Python — молча
  }
  process.exit(2);
}

export function write_json(p, obj) {
  // Атомарная запись: <файл>.tmp.<pid> и rename. Хуки одного события идут параллельно,
  // на Windows rename падает, пока файл открыт читателем, — ошибка тихо пропускается.
  // true — записано.
  p = _norm(p);
  const name = path.basename(p);
  if (!name || name === ".") throw new TypeError(`${p} has an empty name`); // with_name Python
  const tmp = path.join(path.dirname(p), `${name}.tmp.${process.pid}`);
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const text = dumps(obj, { ensure_ascii: false });
    if (!_well_formed(text)) throw new TypeError("surrogates not allowed");
    fs.writeFileSync(tmp, text);
    fs.renameSync(tmp, p);
    return true;
  } catch {
    try {
      fs.unlinkSync(tmp);
    } catch {
      // нечего удалять
    }
    return false;
  }
}

export function cleanup_status(days = STATUS_TTL_DAYS) {
  // Удалить старые файлы мода, кеш ultracode, отметки context-guard и model-advisor, брошенные tmp.
  const cutoff = _time() - days * DAY;
  for (const pattern of ["*.state.json", "*.ultra.json", "*.ctx-mark", "*.compact.json", "*.advice.json", "*.tmp.*"]) {
    for (const f of _glob(status_dir(), pattern)) {
      try {
        if (_mtime(f) < cutoff) fs.unlinkSync(f);
      } catch {
        // занят, исчез, каталог — пропуск
      }
    }
  }
}

// --- сжатие -----------------------------------------------------------------------------------

export function note_compact(sid, at) {
  // Отметка «сжатие прошло, началось в at» — <sid>.compact.json. Пишет context-guard на SessionStart
  // после сжатия. Первый промпт после сжатия приходит через доли секунды, а Claude Code дописывает
  // транскрипт с задержкой: хук видел последний ответ до сжатия, а не границу (319k вместо 28k,
  // 03.10.2026). Показания старше отметки _g_context не берёт.
  if (_truthy(sid)) write_json(path.join(status_dir(), `${safe_sid(sid)}.compact.json`), { at });
}

export function read_compact(sid) {
  const data = _truthy(sid) ? _load_json(path.join(status_dir(), `${safe_sid(sid)}.compact.json`)) : null;
  return _is_dict(data) ? _num(_get(data, "at")) : null;
}

// --- мелочи ------------------------------------------------------------------------------------

export function _num(x) {
  return typeof x === "number" ? x : null;
}

export function _count(x) {
  // Целое ≥ 0 (токены, окно) или null.
  const n = _num(x);
  return n !== null && n >= 0 && n < 1e12 ? _py_int(n) : null;
}

export function _load_json(p) {
  try {
    return JSON.parse(UTF8.decode(fs.readFileSync(p)));
  } catch {
    return null;
  }
}

// datetime.fromisoformat Python 3.11 (C-версия, _datetimemodule.c) и .timestamp(): разбор по байтам
// UTF-8, те же допуски и проверки; без пояса — местное время, как local_to_seconds.
const DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const DAYS_BEFORE_MONTH = [0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
const EPOCH_SECONDS = 719163 * 24 * 60 * 60;

function _is_leap(y) {
  return y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
}

function _days_in_month(y, m) {
  return m === 2 && _is_leap(y) ? 29 : DAYS_IN_MONTH[m];
}

function _ymd_to_ord(y, m, d) {
  const z = y - 1;
  const before_year = z * 365 + Math.trunc(z / 4) - Math.trunc(z / 100) + Math.trunc(z / 400);
  return before_year + DAYS_BEFORE_MONTH[m] + (m > 2 && _is_leap(y) ? 1 : 0) + d;
}

function _ord_to_ymd(ordinal) {
  if (!(ordinal >= 1)) throw new RangeError("ordinal out of range"); // в C — неопределённое поведение
  let n = ordinal - 1;
  const n400 = Math.trunc(n / 146097);
  n %= 146097;
  const n100 = Math.trunc(n / 36524);
  n %= 36524;
  const n4 = Math.trunc(n / 1461);
  n %= 1461;
  const n1 = Math.trunc(n / 365);
  n %= 365;
  const year = n400 * 400 + 1 + n100 * 100 + n4 * 4 + n1;
  if (n1 === 4 || n100 === 4) return [year - 1, 12, 31];
  const leap = n1 === 3 && (n4 !== 24 || n100 === 3);
  let month = (n + 50) >> 5;
  let preceding = DAYS_BEFORE_MONTH[month] + (month > 2 && leap ? 1 : 0);
  if (preceding > n) {
    month -= 1;
    preceding -= _days_in_month(year, month);
  }
  return [year, month, n - preceding + 1];
}

function _iso_to_ymd(year, week, day) {
  if (week <= 0 || week >= 53) {
    const first_weekday = (_ymd_to_ord(year, 1, 1) + 6) % 7;
    if (!(week === 53 && (first_weekday === 3 || (first_weekday === 2 && _is_leap(year))))) {
      throw new RangeError("Invalid week");
    }
  }
  if (day <= 0 || day >= 8) throw new RangeError("Invalid weekday");
  const first_day = _ymd_to_ord(year, 1, 1);
  const first_weekday = (first_day + 6) % 7;
  let monday = first_day - first_weekday;
  if (first_weekday > 3) monday += 7;
  return _ord_to_ymd(monday + (week - 1) * 7 + day - 1);
}

function _is_digit(c) {
  return c >= 0x30 && c <= 0x39;
}

function _digits(at, p, n) {
  let v = 0;
  for (let i = 0; i < n; i++) {
    const c = at(p++);
    if (!_is_digit(c)) return null;
    v = v * 10 + (c - 0x30);
  }
  return [p, v];
}

function _iso_separator(at, len) {
  if (len === 7) return 7;
  if (at(4) === 0x2d) {
    if (at(5) !== 0x57) return 10; // YYYY-MM-DD
    if (len < 8) return -1;
    if (len > 8 && at(8) === 0x2d) {
      if (len === 9) return -1;
      return len > 10 && _is_digit(at(10)) ? 8 : 10;
    }
    return 8;
  }
  if (at(4) !== 0x57) return 8; // YYYYMMDD
  let idx = 7;
  while (idx < len && _is_digit(at(idx))) idx++;
  if (idx < 9) return idx;
  return idx % 2 === 0 ? 7 : 8;
}

function _hh_mm_ss_ff(at, p, p_end) {
  // [код, [час, мин, сек, мкс]]: 0 — конец строки, 1 — дальше что-то есть, < 0 — ошибка.
  const vals = [0, 0, 0, 0];
  let has_sep = true;
  for (let i = 0; i < 3; i++) {
    const r = _digits(at, p, 2);
    if (!r) return [-3, vals];
    [p, vals[i]] = r;
    const c = at(p++);
    if (i === 0) has_sep = c === 0x3a;
    if (p >= p_end) return [c !== 0 ? 1 : 0, vals];
    if (has_sep && c === 0x3a) continue;
    if (c === 0x2e || c === 0x2c) break;
    if (has_sep) return [-4, vals];
    p--;
  }
  const to_parse = Math.min(p_end - p, 6);
  const r = _digits(at, p, to_parse);
  if (!r) return [-3, vals];
  [p, vals[3]] = r;
  if (to_parse < 6) vals[3] *= [100000, 10000, 1000, 100, 10][to_parse - 1];
  while (_is_digit(at(p))) p++;
  return [at(p) !== 0 ? 1 : 0, vals];
}

function _utc_to_seconds(y, mo, d, h, mi, s) {
  if (!(y >= 1 && y <= 9999)) throw new RangeError(`year ${y} is out of range`);
  return ((_ymd_to_ord(y, mo, d) * 24 + h) * 60 + mi) * 60 + s;
}

function _local(u) {
  const d = new Date((u - EPOCH_SECONDS) * 1000);
  if (Number.isNaN(d.getTime())) throw new RangeError("timestamp out of range for platform time_t");
  return _utc_to_seconds(d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours(), d.getMinutes(),
    d.getSeconds());
}

function _local_to_seconds(y, mo, d, h, mi, s) {
  // local_to_seconds C-версии при fold=0: в перекрытии — раннее время, в разрыве — max(u1, u2).
  const t = _utc_to_seconds(y, mo, d, h, mi, s);
  const a = _local(t) - t;
  const u1 = t - a;
  const t1 = _local(u1);
  let b;
  if (t1 === t) {
    const u2 = u1 - 24 * 3600;
    b = _local(u2) - u2;
    if (a === b) return u1;
  } else {
    b = t1 - u1;
  }
  const u2 = t - b;
  if (_local(u2) === t) return u2;
  if (t1 === t) return u1;
  return Math.max(u1, u2);
}

function _fromisoformat_ts(value) {
  const bad = () => new RangeError("Invalid isoformat string");
  const cps = Array.from(value); // кодовые точки, как индексы str Python
  if (cps.length < 7) throw bad();
  for (const pos of [7, 8, 10]) { // одиночный суррогат-разделитель C-версия заменяет на T
    if (pos > cps.length) break;
    if (pos < cps.length && cps[pos].length === 1 && /[\uD800-\uDFFF]/.test(cps[pos])) {
      cps[pos] = "T";
      break;
    }
  }
  const clean = cps.join("");
  if (!_well_formed(clean)) throw bad();
  const b = Buffer.from(clean, "utf8");
  const len = b.length;
  const at = (i) => (i >= 0 && i < len ? b[i] : 0);
  const sep = _iso_separator(at, len);
  if (sep < 0) throw bad();

  let r = _digits(at, 0, 4);
  if (!r) throw bad();
  let [p, year] = r;
  const uses_sep = at(p) === 0x2d;
  if (uses_sep) p++;
  let month;
  let day;
  if (at(p) === 0x57) { // YYYY-Www-D
    r = _digits(at, p + 1, 2);
    if (!r) throw bad();
    let week;
    [p, week] = r;
    let iso_day = 1;
    if (p < sep) {
      if (uses_sep && at(p++) !== 0x2d) throw bad();
      r = _digits(at, p, 1);
      if (!r) throw bad();
      [p, iso_day] = r;
    }
    [year, month, day] = _iso_to_ymd(year, week, iso_day);
  } else {
    r = _digits(at, p, 2);
    if (!r) throw bad();
    [p, month] = r;
    if (uses_sep && at(p++) !== 0x2d) throw bad();
    r = _digits(at, p, 2);
    if (!r) throw bad();
    [p, day] = r;
  }

  let time = [0, 0, 0, 0];
  let tz = null; // смещение пояса в мкс; null — без пояса
  if (len > sep) {
    const lead = at(sep); // разделитель — любой символ, длина — по ведущему байту UTF-8
    let q = sep + ((lead & 0x80) === 0 ? 1 : (lead & 0xf0) === 0xe0 ? 3 : (lead & 0xf0) === 0xf0 ? 4 : 2);
    let tz_pos = q;
    do {
      const c = at(tz_pos);
      if (c === 0x5a || c === 0x2b || c === 0x2d) break;
    } while (++tz_pos < len);
    let rv;
    [rv, time] = _hh_mm_ss_ff(at, q, tz_pos);
    if (rv < 0) throw bad();
    if (tz_pos === len) {
      if (rv === 1) throw bad();
    } else if (at(tz_pos) === 0x5a) {
      if (at(tz_pos + 1) !== 0) throw bad();
      tz = 0;
    } else {
      const sign = at(tz_pos) === 0x2d ? -1 : 1;
      const [rv_tz, z] = _hh_mm_ss_ff(at, tz_pos + 1, len);
      if (rv_tz) throw bad();
      const seconds = sign * (z[0] * 3600 + z[1] * 60 + z[2]);
      tz = seconds === 0 ? 0 : seconds * 1e6 + sign * z[3]; // ровно 0 с — UTC, мкс не в счёт
      if (tz <= -86400e6 || tz >= 86400e6) throw new RangeError("offset must be strictly between -24h and 24h");
    }
  }
  const [hour, minute, second, us] = time;
  if (!(year >= 1 && year <= 9999) || !(month >= 1 && month <= 12) || !(day >= 1 && day <= _days_in_month(year, month))
    || !(hour <= 23) || !(minute <= 59) || !(second <= 59)) throw bad();

  if (tz === null) return _local_to_seconds(year, month, day, hour, minute, second) - EPOCH_SECONDS + us / 1e6;
  const secs = (_ymd_to_ord(year, month, day) - 719163) * 86400 + hour * 3600 + minute * 60 + second;
  const total = BigInt(secs) * 1_000_000n + BigInt(us - tz); // timedelta.total_seconds: мкс / 10**6
  if (total >= -(2n ** 53n) && total <= 2n ** 53n) return Number(total) / 1e6;
  return Number(total / 1_000_000n) + Number(total % 1_000_000n) / 1e6;
}

export function _iso_ts(value) {
  // ISO-время записи транскрипта → секунды эпохи (как fromisoformat Python 3.11 после замены «Z»).
  if (typeof value !== "string") return null;
  try {
    return _fromisoformat_ts(value.replaceAll("Z", "+00:00"));
  } catch {
    return null;
  }
}

function _block(data, name) {
  const block = _get(data, name);
  return _is_dict(block) ? block : {};
}

function _at(block, data) {
  // Время показания блока мода: его at, иначе ts файла.
  return _num(_get(block, "at")) || _num(_get(data, "ts"));
}

function _pick(cands) {
  // Самое свежее показание [at, src, value]; ничья в пределах TIE — по PRIORITY.
  cands = cands.filter(Boolean);
  if (!cands.length) return null;
  const top = Math.max(...cands.map((c) => c[0] || 0));
  let best = null;
  for (const c of cands) {
    if ((c[0] || 0) >= top - TIE && (best === null || (PRIORITY[c[1]] || 0) > (PRIORITY[best[1]] || 0))) best = c;
  }
  return best;
}

function _same_model(a, b) {
  const norm = (m) => _py_str(_or(m, "")).replace(/\[1m\](?=\n?$)/u, "").toLowerCase();
  return _truthy(a) && norm(a) === norm(b);
}

export function _ascii_digits(s) {
  // Цифры любой письменности → ASCII, как float() Python (группы Nd — по 10 подряд, с нуля).
  let out = "";
  for (const ch of s) {
    let c = ch.codePointAt(0);
    if (c < 0x80) {
      out += ch;
      continue;
    }
    let zero = c;
    while (/\p{Nd}/u.test(String.fromCodePoint(zero - 1))) zero--;
    out += String((c - zero) % 10);
  }
  return out;
}

export function parse_tokens(value) {
  // 900000, «900k», «0.9m», «650к», «0,65М»; строка-число < 5000 без единиц — тысячи
  // (модели пишут «100», имея в виду 100k). «auto», пусто и мусор → null.
  if (typeof value === "boolean") return null;
  if (typeof value === "number") return value > 0 && value < 1e12 ? _py_int(value) : null;
  if (typeof value !== "string") return null;
  const m = TOKENS_RE.exec(value);
  if (!m) return null;
  const n = Number(_ascii_digits(m[1]).replace(",", "."));
  const mult = (Object.hasOwn(UNITS, m[2].toLowerCase()) && UNITS[m[2].toLowerCase()]) || (n < 5000 ? 1_000 : 1);
  const r = _py_int(_py_round(n * mult));
  return r > 0 ? r : null;
}

// --- окно и порог автосжатия -------------------------------------------------------------------

function _window_default(model) {
  const override = parse_tokens(_env("CLAUDE_CODE_MAX_CONTEXT_TOKENS"));
  if (override) return [override, "env"];
  if (_env("CLAUDE_CODE_DISABLE_1M_CONTEXT") === "1") return [200_000, "env"];
  if (!_truthy(model)) return [1_000_000, "default"]; // модель ещё не известна — у владельца по умолчанию окно 1M
  return [NATIVE_1M.test(_py_str(model)) ? 1_000_000 : 200_000, "default"];
}

export function window_for(model) {
  // Окно модели без мода и статусной строки: env, затем по имени модели.
  return _window_default(model)[0];
}

export function settings_value(cwd, key) {
  // Значение из настроек: локальные проекта > проекта > пользователя (<claude>/settings.json).
  const files = [];
  if (typeof cwd === "string" && cwd) {
    files.push(path.join(cwd, ".claude", "settings.local.json"), path.join(cwd, ".claude", "settings.json"));
  }
  files.push(path.join(claude_dir(), "settings.json"));
  for (const f of files) {
    const data = _load_json(f);
    if (_is_dict(data) && Object.hasOwn(data, key)) return data[key];
  }
  return null;
}

function _autocompact(model, cwd, window = null) {
  window = window || window_for(model);
  let explicit = parse_tokens(_env("CLAUDE_CODE_AUTO_COMPACT_WINDOW"));
  let src = "env";
  if (!explicit) {
    explicit = parse_tokens(settings_value(cwd, "autoCompactWindow"));
    src = "settings";
  }
  if (!explicit) src = "default";
  return [Math.max(1, Math.min(explicit || window, window) - AUTOCOMPACT_RESERVE), src];
}

export function autocompact_at(model, cwd = null, window = null) {
  // Сколько токенов в контексте, когда Claude Code начнёт автосжатие (если мод его не дал).
  return _autocompact(model, cwd, window)[0];
}

// --- источники ---------------------------------------------------------------------------------

export function read_mod(sid) {
  // Файл мода session-state <sid>.state.json — объект или null (нет, пусто, битый).
  if (!_truthy(sid)) return null;
  const data = _load_json(path.join(status_dir(), `${safe_sid(sid)}.state.json`));
  return _is_dict(data) ? data : null;
}

export function read_statusline(sid) {
  // Файл статусной строки <sid>.json — объект с добавленным "at" (mtime: сам файл времени не
  // знает) или null.
  if (!_truthy(sid)) return null;
  const p = path.join(status_dir(), `${safe_sid(sid)}.json`);
  let mtime;
  try {
    mtime = _mtime(p);
  } catch {
    return null;
  }
  const data = _load_json(p);
  return _is_dict(data) ? { ...data, at: mtime } : null;
}

function _pread(fd, n, pos) {
  const buf = Buffer.allocUnsafe(n);
  let got = 0;
  while (got < n) {
    const k = fs.readSync(fd, buf, got, n - got, pos + got);
    if (!k) break;
    got += k;
  }
  return got < n ? buf.subarray(0, got) : buf;
}

function _split_lines(buf) {
  // bytes.split(b"\n") без пустых строк, кроме первой (она — хвост куска): читатель их всё равно
  // пропускает, а отрезок на каждую из миллионов пустых строк — в разы медленнее Python.
  const lines = [];
  let start = 0;
  for (let i = buf.indexOf(0x0a); i !== -1; i = buf.indexOf(0x0a, start)) {
    if (i > start || !lines.length) lines.push(buf.subarray(start, i));
    start = i + 1;
  }
  if (start < buf.length || !lines.length) lines.push(buf.subarray(start));
  return lines;
}

const EMPTY = Buffer.alloc(0);

export function* entries_backwards(p, max_bytes = TAIL_MAX, needles = null) {
  // Записи транскрипта (объекты) от последней к первой, кусками по 1 МБ, не дальше max_bytes от
  // конца. needles — строки или Buffer: разбирать только строки, где есть хоть одна (быстрый отсев).
  let fd;
  try {
    fd = fs.openSync(p, "r");
  } catch {
    return;
  }
  try {
    let pos;
    try {
      const st = fs.fstatSync(fd);
      if (st.isDirectory()) return; // Python не откроет каталог
      pos = st.size;
    } catch {
      return;
    }
    const stop = Math.max(0, pos - max_bytes);
    let tail = EMPTY;
    while (pos > stop) {
      const step = Math.min(1 << 20, pos - stop);
      pos -= step;
      let lines;
      try {
        lines = _split_lines(Buffer.concat([_pread(fd, step, pos), tail]));
      } catch {
        return;
      }
      tail = pos > stop ? lines.shift() : EMPTY; // первая строка куска может быть обрезана
      for (let i = lines.length - 1; i >= 0; i--) {
        const raw = lines[i];
        if (!raw.length || (needles && needles.length && !needles.some((n) => raw.includes(n)))) continue;
        let entry;
        try {
          entry = JSON.parse(UTF8.decode(raw));
        } catch {
          continue;
        }
        if (_is_dict(entry)) yield entry;
      }
    }
  } finally {
    try {
      fs.closeSync(fd);
    } catch {
      // уже закрыт
    }
  }
}

function _attachment_model(att) {
  const identity = _get(att, "identity");
  const mid = _get(identity, "modelId");
  if (typeof mid === "string" && mid) return mid;
  const m = MODEL_IN_TEXT.exec(_py_str(_or(_get(att, "text"), "")));
  return m ? m[1] : null;
}

const SCAN_NEEDLES = [Buffer.from('"assistant"'), Buffer.from('"model"'), Buffer.from("compact_boundary")];

function _scan_transcript(p) {
  // Один проход с конца: {tokens: [n|null, at, после_сжатия], model: [id, at],
  // effort: [значение, at] | null}. Стоп — на последнем ответе основной ветки.
  const out = {};
  if (!_truthy(p)) return out;
  for (const e of entries_backwards(p, TAIL_MAX, SCAN_NEEDLES)) {
    if (_truthy(_get(e, "isSidechain"))) continue;
    const kind = _get(e, "type");
    const at = _iso_ts(_get(e, "timestamp"));
    if (kind === "system" && _get(e, "subtype") === "compact_boundary") {
      if (!("tokens" in out)) { // сразу после сжатия ответов ещё нет: размер — что сжатие оставило
        const post = _count(_get(_block(e, "compactMetadata"), "postTokens"));
        out.tokens = [post, at, true];
      }
    } else if (kind === "attachment") { // вложение model — на старте, после сжатия и после /model
      const att = _block(e, "attachment");
      if (_get(att, "type") === "model" && !("model" in out)) {
        const mid = _attachment_model(att);
        if (mid) out.model = [mid, at];
      }
    } else if (kind === "assistant") {
      const msg = _block(e, "message");
      const model = _get(msg, "model");
      if (model === "<synthetic>") continue; // служебный ответ: модели нет, токены по нулям
      const usage = _block(msg, "usage");
      let tokens = 0; // без output
      for (const k of CONTEXT_KEYS) tokens += _count(_get(usage, k)) || 0;
      if (!("tokens" in out) && tokens) out.tokens = [tokens, at, false];
      if (!("model" in out) && typeof model === "string" && model) out.model = [model, at];
      if (!("effort" in out)) out.effort = Object.hasOwn(e, "effort") ? [e.effort, at] : null;
    }
    if ("tokens" in out && "model" in out && "effort" in out) break;
  }
  return out;
}

export function _ultra_scan(p, sid) {
  // Ultracode по транскрипту с кешем <sid>.ultra.json {path, offset, value}: дочитывается только
  // прирост. Другой путь или файл короче offset — полный проход. Нет событий — false, ошибка — null.
  let size;
  try {
    const st = fs.statSync(p);
    if (!st.isFile()) return null;
    size = st.size;
  } catch {
    return null;
  }
  const cache_path = _truthy(sid) ? path.join(status_dir(), `${safe_sid(sid)}.ultra.json`) : null;
  const cache = cache_path ? _load_json(cache_path) : null;
  let offset = 0;
  let value = null;
  const cached = _get(cache, "value");
  // value in (True, False) у Python — сравнение ==: 1 и 0 тоже проходят.
  if (_get(cache, "path") === _py_str(p) && [true, false, 1, 0].includes(cached)) {
    const off = _num(_get(cache, "offset"));
    if (off !== null && off >= 0 && off <= size) {
      offset = _py_int(off);
      value = cached;
    }
  }
  let done = offset;
  try {
    const fd = fs.openSync(p, "r");
    try {
      let pos = offset;
      let buf = EMPTY;
      for (;;) {
        const chunk = _pread(fd, ULTRA_CHUNK, pos);
        if (!chunk.length) break;
        pos += chunk.length;
        buf = buf.length ? Buffer.concat([buf, chunk]) : chunk;
        const cut = buf.lastIndexOf(0x0a) + 1; // только целые строки: недописанную прочтём в следующий раз
        if (!cut) continue;
        const head = buf.subarray(0, cut);
        if (head.includes("Ultracode") || head.includes("ultra_effort_")) {
          let last = null;
          for (const m of head.toString("latin1").matchAll(ULTRA_EVENT)) last = m;
          if (last) value = last[1] === "on" || last[2] === "enter";
        }
        done += cut;
        buf = buf.subarray(cut);
      }
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
  if (value === null) value = false;
  if (cache_path && (done !== offset || !_truthy(cache))) {
    write_json(cache_path, { path: _py_str(p), offset: done, value });
  }
  return value;
}

class _Sources {
  // Источники одной сессии: каждый читается не больше раза и только когда нужен.

  constructor(inp = null, sid = null, transcript = null, cwd = null, now = null) {
    inp = _is_dict(inp) ? inp : {};
    this.inp = inp;
    this.sid = _or(sid, _get(inp, "session_id"), null);
    this.transcript = _or(transcript, _get(inp, "transcript_path"), null);
    this.cwd = _or(cwd, _get(inp, "cwd"), null);
    this.event = _get(inp, "hook_event_name");
    this.now = now === null || now === undefined ? _time() : now;
    this._cache = new Map();
  }

  get(key, fn) {
    if (!this._cache.has(key)) {
      let value;
      try {
        value = fn();
      } catch { // хук не должен падать ни на каком источнике
        value = null;
      }
      this._cache.set(key, value === undefined ? null : value);
    }
    return this._cache.get(key);
  }

  get mod() {
    return this.get("mod", () => read_mod(this.sid));
  }

  get statusline() {
    return this.get("statusline", () => read_statusline(this.sid));
  }

  get compact_at() {
    return this.get("compact", () => read_compact(this.sid));
  }

  get scan() {
    return this.get("scan", () => _scan_transcript(this.transcript)) || {};
  }
}

// --- группы полей ------------------------------------------------------------------------------

function _model(s) {
  const pick = () => {
    const mod = _block(s.mod, "model");
    const st = s.statusline || {};
    const tr = _get(s.scan, "model");
    const cands = [];
    const mid = _get(mod, "id");
    if (typeof mid === "string" && mid) cands.push([_at(mod, s.mod), "mod", mid]);
    if (tr) cands.push([tr[1], "transcript", tr[0]]);
    const st_model = _get(st, "model");
    if (typeof st_model === "string" && st_model) cands.push([st.at, "statusline", st_model]);
    const best = _pick(cands);
    return best ? [best[2], best[1]] : [null, null];
  };
  return s.get("model", pick) || [null, null];
}

function _g_model(s) {
  const [model, src] = _model(s);
  const st = s.statusline || {};
  let name = model;
  let name_src = src;
  const st_name = _get(st, "name");
  if (_truthy(model) && typeof st_name === "string" && st_name && _same_model(_get(st, "model"), model)) {
    name = st_name;
    name_src = "statusline";
  }
  return { model, model_name: name, src: { model: src, model_name: name_src } };
}

function _g_effort(s) {
  let hook = _get(s.inp, "effort");
  if (_is_dict(hook)) hook = _get(hook, "level");
  if (hook !== null && hook !== "") return { effort: hook, src: { effort: "hook" } };
  const env = _env("CLAUDE_EFFORT");
  if (env && _truthy(s.event) && s.event !== "UserPromptSubmit") { // на промпте там значение со старта процесса
    return { effort: env, src: { effort: "env" } };
  }
  // null у источника — тоже показание: модель без effort (Haiku).
  const cands = [];
  const mod = _block(s.mod, "effort");
  if (Object.hasOwn(mod, "level")) cands.push([_at(mod, s.mod), "mod", mod.level]);
  const st = s.statusline;
  if (st && Object.hasOwn(st, "effort")) cands.push([st.at, "statusline", st.effort]);
  const tr = _get(s.scan, "effort");
  if (tr) cands.push([tr[1], "transcript", tr[0]]);
  const best = _pick(cands);
  return { effort: best ? best[2] : null, src: { effort: best ? best[1] : null } };
}

function _g_ultracode(s) {
  const value = _truthy(s.transcript) ? _ultra_scan(s.transcript, s.sid) : null;
  if (value !== null) return { ultracode: value, src: { ultracode: "transcript" } };
  const st = s.statusline || {};
  const st_value = _get(st, "ultracode");
  if (typeof st_value === "boolean") return { ultracode: st_value, src: { ultracode: "statusline" } };
  return { ultracode: null, src: { ultracode: null } };
}

function _g_context(s) {
  const mod = s.mod || {};
  const ctx = _block(mod, "context");
  const at_mod = _at(ctx, mod);
  const st = s.statusline || {};
  const model = _model(s)[0];

  // Окно: мод или статусная строка (что свежее), иначе по модели.
  let cands = [];
  if (_count(_get(ctx, "window"))) cands.push([at_mod, "mod", _count(ctx.window)]);
  if (_count(_get(st, "window"))) cands.push([st.at, "statusline", _count(st.window)]);
  let best = _pick(cands);
  const [window, window_src] = best ? [best[2], best[1]] : _window_default(model);

  // Токены: мод или транскрипт (что свежее). Статусная строка — только запасной: её mtime
  // обновляется каждую секунду, а точность — 1 % окна.
  // Граница сжатия без postTokens — тоже показание: сжатие было, и замер мода до него устарел.
  cands = [];
  if (_count(_get(ctx, "tokens")) !== null) cands.push([at_mod, "mod", [_count(ctx.tokens), false]]);
  const tr = _get(s.scan, "tokens");
  if (tr && (tr[0] !== null || tr[2])) cands.push([tr[1], "transcript", [tr[0], tr[2]]]);
  // Показания до прошедшего сжатия устарели (note_compact), и строка тогда — тоже по ним.
  const cut = s.compact_at;
  const stale = cut !== null && !cands.some((c) => (c[0] || 0) >= cut);
  best = stale ? null : _pick(cands);
  let tokens = null;
  let after_compact = stale;
  let tokens_src = null;
  if (best) {
    [tokens, after_compact] = best[2];
    tokens_src = best[1];
  }
  const used_pct = _num(_get(st, "used_pct"));
  if (tokens === null && !stale && used_pct !== null && (_count(_get(st, "window")) || window)) {
    tokens = _py_int(used_pct * (_count(_get(st, "window")) || window) / 100);
    tokens_src = "statusline~";
  }

  let auto = _count(_get(ctx, "auto_compact_at"));
  let auto_src;
  if (auto) auto_src = "mod";
  else [auto, auto_src] = _autocompact(model, s.cwd, window);
  let enabled = _get(ctx, "auto_compact_enabled");
  if (typeof enabled !== "boolean") enabled = _count(_get(ctx, "auto_compact_at")) ? true : null;
  if (enabled === false) {
    // Мод сказал «автосжатие выключено»: порог по формуле был бы выдумкой.
    auto = null;
    auto_src = "mod";
  }
  return {
    tokens, after_compact, window, auto_compact_at: auto, auto_compact_enabled: enabled,
    fill_pct: tokens !== null && auto ? _floordiv(tokens * 100, auto) : null,
    src: { tokens: tokens_src, window: window_src, auto_compact_at: auto_src,
      auto_compact_enabled: enabled !== null ? "mod" : null },
  };
}

function _context_fallback(s) {
  const model = _model(s)[0];
  const [window, src] = _window_default(model);
  return {
    tokens: null, after_compact: false, window, auto_compact_at: Math.max(1, window - AUTOCOMPACT_RESERVE),
    auto_compact_enabled: null, fill_pct: null,
    src: { tokens: null, window: src, auto_compact_at: "default", auto_compact_enabled: null },
  };
}

function _group(s, fn, fallback) {
  try {
    return fn(s);
  } catch {
    return fallback(s);
  }
}

function _empty(...names) {
  return () => ({ ...Object.fromEntries(names.map((n) => [n, null])), src: Object.fromEntries(names.map((n) => [n, null])) });
}

// --- лимиты и дневная норма --------------------------------------------------------------------

function* _limit_readings() {
  // [src, окно, {used_percentage, resets_at}, at] из файлов мода за 8 суток и rate-limits.json.
  const d = status_dir();
  const wall = _time();
  for (const p of _glob(d, "*.state.json")) {
    let mtime;
    try {
      mtime = _mtime(p);
    } catch {
      continue;
    }
    if (wall - mtime > LIMITS_MAX_AGE) continue;
    const data = _load_json(p);
    const rl = _block(data, "rate_limits");
    for (const name of WINDOWS) {
      const w = _block(rl, name);
      if (_truthy(w)) yield ["mod", name, w, _num(_get(w, "at")) || _num(_get(data, "ts")) || mtime];
    }
  }
  const p = path.join(d, "rate-limits.json");
  const data = _load_json(p);
  if (_is_dict(data)) {
    let mtime;
    try {
      mtime = _mtime(p);
    } catch {
      mtime = null;
    }
    for (const name of WINDOWS) {
      const w = _block(data, name);
      if (_truthy(w)) yield ["statusline", name, w, _num(_get(data, "ts")) || mtime];
    }
  }
}

function _tuple_gt(a, b) {
  for (let i = 0; i < a.length; i++) {
    if (a[i] > b[i]) return true;
    if (a[i] < b[i]) return false;
  }
  return false;
}

export function read_limits(now = null) {
  // Лимиты подписки {five_hour: {...}, seven_day: {...}} — в каждом used, resets_at, at,
  // age (с), src ("mod" | "statusline"). Только окна со сбросом впереди: внутри окна процент только
  // растёт, поэтому старое показание — нижняя граница и годно до сброса. Лимит общий для аккаунта:
  // по окну берётся показание с поздним сбросом (с точностью до минуты), потом с большим процентом.
  now = now === null || now === undefined ? _time() : now;
  const best = new Map();
  try {
    for (const [src, name, w, at] of _limit_readings()) {
      const used = _num(_get(w, "used_percentage"));
      const reset = _num(_get(w, "resets_at"));
      if (used === null || reset === null) continue;
      const key = [_py_round(reset / 60), used, at || 0];
      if (!best.has(name) || _tuple_gt(key, best.get(name)[0])) {
        best.set(name, [key, { used, resets_at: reset, at, age: at ? Math.max(0.0, now - at) : null, src }]);
      }
    }
  } catch {
    return {};
  }
  const out = {};
  for (const [name, [, v]] of best) if (v.resets_at > now) out[name] = v;
  return out;
}

function _isoweekday(t) {
  // datetime.fromtimestamp(t).isoweekday(): мкс — округлением к чётному, год вне 1..9999 — ошибка.
  if (!Number.isFinite(t)) throw new RangeError("Invalid value");
  let sec = Math.trunc(t);
  const us = _py_round((t - sec) * 1e6);
  if (us >= 1e6) sec += 1;
  else if (us < 0) sec -= 1;
  const d = new Date(sec * 1000);
  if (Number.isNaN(d.getTime())) throw new RangeError("timestamp out of range for platform time_t");
  const year = d.getFullYear();
  if (year < 1 || year > 9999) throw new RangeError(`year ${year} is out of range`);
  return d.getDay() || 7;
}

export function day_budget(d7, now = null) {
  // Дневная норма недельного лимита — формула статусной строки («сегодня S/N%»): день
  // начинается во время сброса недели; норма — остаток на начало дня ÷ оставшиеся рабочие дни
  // (пн–пт) до сброса, выходной — один день. Начало дня — week-budget.json (общий со статусной
  // строкой). Новый день модуль записывает сам (в Desktop статусной строки нет), но только по
  // показанию, снятому уже в этих сутках: вчерашнее занизило бы начало дня.
  // d7 — окно seven_day из read_limits (used, resets_at, at). Результат — {spent, norm, start,
  // day_start} или null.
  try {
    if (!_is_dict(d7)) return null;
    let used = _num(_get(d7, "used"));
    if (used === null) used = _num(_get(d7, "used_percentage"));
    const reset = _num(_get(d7, "resets_at"));
    if (used === null || reset === null) return null;
    now = now === null || now === undefined ? _time() : now;
    _py_int(reset); // int(reset) у Python: бесконечность — ошибка
    const ws = reset - WEEK;
    const ds = ws + _py_int(_floordiv(now - ws, DAY)) * DAY;
    const p = path.join(status_dir(), "week-budget.json");
    let st = _load_json(p);
    st = _is_dict(st) ? st : {};
    let day_start = _get(st, "day_start");
    if (typeof day_start === "boolean") day_start = Number(day_start); // True == 1 у Python
    let start;
    if (day_start === ds && _num(_get(st, "start_pct")) !== null) {
      start = st.start_pct;
    } else {
      start = ds === ws ? 0 : used;
      const at = _num(_get(d7, "at"));
      if (at !== null && at >= ds) write_json(p, { day_start: ds, start_pct: start });
    }
    let days = 0;
    for (let i = 0; i < 7; i++) if (ws + i * DAY >= ds && _isoweekday(ws + i * DAY) <= 5) days += 1;
    days += _isoweekday(ds) > 5 ? 1 : 0;
    const norm = days ? (100 - start) / days : 100 - start;
    return { spent: Math.max(0, used - start), norm, start, day_start: ds };
  } catch {
    return null;
  }
}

// --- главное -----------------------------------------------------------------------------------

export function session_state(inp = null, { fields = FIELDS_ALL, sid = null, transcript = null, cwd = null,
  now = null } = {}) {
  // Состояние сессии. inp — вход хука (session_id, transcript_path, cwd, effort,
  // hook_event_name); sid/transcript/cwd — замена полей входа. Считаются только группы из fields:
  //   model     — model, model_name;
  //   effort    — effort;
  //   ultracode — ultracode;
  //   context   — tokens, after_compact, window, auto_compact_at, auto_compact_enabled, fill_pct;
  //   limits    — limits (read_limits);
  //   day       — day (day_budget по limits.seven_day).
  // src — источник каждого поля групп model/effort/ultracode/context (mod, statusline,
  // statusline~, transcript, hook, env, settings, default; null — данных нет); у лимитов src —
  // внутри каждого окна. Поля невычисленных групп в результате отсутствуют.
  if (typeof fields === "string") fields = [fields];
  fields = new Set(fields || []);
  const s = new _Sources(inp, sid, transcript, cwd, now);
  const out = {};
  const src = {};
  const groups = [["model", _g_model, _empty("model", "model_name")],
    ["effort", _g_effort, _empty("effort")],
    ["ultracode", _g_ultracode, _empty("ultracode")],
    ["context", _g_context, _context_fallback]];
  for (const [name, fn, fallback] of groups) {
    if (fields.has(name)) {
      const { src: part_src, ...part } = _group(s, fn, fallback);
      Object.assign(src, part_src);
      Object.assign(out, part);
    }
  }
  if (fields.has("limits") || fields.has("day")) {
    const limits = read_limits(s.now);
    if (fields.has("limits")) out.limits = limits;
    if (fields.has("day")) out.day = day_budget(_get(limits, "seven_day"), s.now);
  }
  out.src = src;
  return out;
}

export function read_model(inp = null, { sid, transcript, cwd, now } = {}) {
  // model, model_name, effort и src.
  return session_state(inp, { fields: ["model", "effort"], sid, transcript, cwd, now });
}

export function read_context(inp = null, { sid, transcript, cwd, now } = {}) {
  // tokens, after_compact, window, auto_compact_at, auto_compact_enabled, fill_pct и src.
  return session_state(inp, { fields: ["context"], sid, transcript, cwd, now });
}

export function read_ultracode(inp = null, { sid, transcript, cwd, now } = {}) {
  // ultracode и src.
  return session_state(inp, { fields: ["ultracode"], sid, transcript, cwd, now });
}

function _is_file(p) {
  // Path.is_file Python 3.11: «нет», «не каталог», петля ссылок — false, прочие ошибки — наружу.
  try {
    return fs.statSync(p).isFile();
  } catch (e) {
    if (["ENOENT", "ENOTDIR", "EBADF", "ELOOP"].includes(e.code)) return false;
    throw e;
  }
}

function _find_transcript(sid) {
  try {
    const projects = path.join(claude_dir(), "projects");
    for (const name of _listdir(projects)) {
      const p = path.join(projects, name, `${sid}.jsonl`);
      if (_is_file(p)) return p;
    }
  } catch {
    // нет каталога или нет доступа
  }
  return null;
}

export function main(argv) {
  if (!argv.length || argv[0] === "-h" || argv[0] === "--help") {
    _print(DOC);
    return 0;
  }
  const transcript = argv.length > 1 ? argv[1] : _find_transcript(argv[0]);
  _print(dumps(session_state(null, { sid: argv[0], transcript }), { ensure_ascii: true, indent: 1 }));
  return 0;
}

function _is_main() {
  try {
    return Boolean(process.argv[1])
      && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (_is_main()) process.exit(main(process.argv.slice(2)));
