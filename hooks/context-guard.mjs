#!/usr/bin/env node
// context-guard: знания раскладываются по местам до того, как сжатие контекста их сотрёт.
// Перенос hooks/context-guard.py на Node с тем же поведением (Python-версия — эталон и запасной путь
// без Node). Описание — DOC ниже, его же печатает запуск с неверными аргументами; разбор — hooks/README.md.
// Не перехватить изнутри, это забота запускающего: Node старше 18 (Node 12 падает ещё на разборе файла;
// scripts/link.py такой не берёт) и NODE_OPTIONS с --require/--import — они выполняются до модуля.

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const DOC = `context-guard: знания раскладываются по местам до того, как сжатие контекста их сотрёт.

Один хук на обе оболочки (терминал в herdr и без него, приложение), одна точка входа на все события
(событие — из hook_event_name во входе), одно состояние: ~/.claude/context-guard/<session_id>.json.
Размер контекста, окно и порог автосжатия — из session-state.mjs рядом (мод session-state, статусная
строка, транскрипт — что свежее). Схема одна; от оболочки зависит только концовка — есть ли панель
herdr ($HERDR_PANE_ID): в ней сжатие и раскладку ведёт агенту herdr; без неё — мод context-compact
(модель после раскладки выполняет \`echo context-compact [--resend <файл>] -- <инструкция>\`, мод после хода
сжимает и присылает запрос заново), а без мода сжимает владелец.

UserPromptSubmit — проверка на старте промпта. Модель уже видит следующую задачу и решает, нужен ли
для неё весь текущий контекст. С BASE_eff (300k) к промпту добавляется вопрос:
  - не нужен — раскладка (/session-extract-knowledge) и сжатие. В панели herdr модель к запросу не
    приступает, а в конце раскладки зовёт \`~/.claude/bin/claude-compact --resend <файл с промптом>\`:
    после сжатия запрос приходит снова. Без панели модель раскладывает знания, выполняет запрос и
    последней строкой просит владельца набрать \`/compact <инструкция>\`;
  - нужен — модель выполняет запрос и пишет в конце ответа «> контекст Nk, проверка отложена до Mk»;
    хук берёт порог из этой строки на следующем промпте (не дальше STEP от текущего). Вызывать \`defer\`
    не нужно: в режиме auto классификатор блокирует его как самомодификацию.
    Вручную: node <kit>/hooks/context-guard.mjs defer <session_id> <токены>   (650000, 650k; «650» без единиц = 650k)
С HARD_eff отложить нельзя. Ниже BASE_eff (после сжатия) отложенный порог сбрасывается. Порог
следующей проверки — в session-status/<session_id>.ctx-mark: статусная строка показывает его «⚑650k».
HARD_eff = min(HARD, порог автосжатия; выключено — 92 % окна), BASE_eff = min(BASE, max(HARD_eff − STEP,
60 % HARD_eff)).

PostToolUse — отметка \`echo knowledge-sorted\` (Bash): знания разложены на нынешнем размере контекста.
Переходное от knowledge_guard: \`echo knowledge-keep-context 700k <зачем>\` — отсрочка проверки до 700k
(не дальше HARD_eff). Пока сжатие отложено (без панели) — напоминание разложить знания.

PreCompact — раскладка перед любым сжатием. Раскладка свежая — сжатие идёт сразу: после отметки
набралось не больше MIN_UNSORTED токенов или после навыка раскладки (session-extract-knowledge,
session-end, с префиксом плагина или без) промптов пользователя не было. Иначе:
  - в панели herdr сжатие отменяется, а агенту после конца хода один раз за PENDING_TTL уходит
    /session-extract-knowledge, который сам вызовет сжатие заново с инструкцией исходного /compact.
    Автосжатие отменяется и пока раскладка идёт или кончилась (сожмёт claude-compact); после одной
    отметки без навыка, у 92 % окна (AUTO_CEILING) и после AUTO_MAX_BLOCKS отказов проходит;
  - без панели ручное /compact откладывается один раз: второй обработчик (аргумент rewake,
    asyncRewake) будит модель — разложить знания и попросить владельца повторить /compact; повторное
    проходит всегда. Автосжатие ждёт отметки, пока до края окна остаётся AUTO_RESERVE, и не больше
    AUTO_MAX_BLOCKS раз.
SessionStart — новый цикл после startup/clear/compact; сжатие прошло без раскладки — подсказка, где
полная запись сессии. Тогда же чистка состояний старше 30 дней.

Не трогает субагентов (agent_id) и сессии без человека (CLAUDE_CODE_ENTRYPOINT=sdk-*). Выключить —
CONTEXT_GUARD_OFF=1 (прежнее KNOWLEDGE_GUARD_OFF=1 тоже). Пороги — CONTEXT_GUARD_BASE, _HARD, _STEP,
_AUTO_CEILING, _MIN_UNSORTED, _KEEP_MAX, _REWAKE_DELAY (у трёх последних запасные — KNOWLEDGE_GUARD_*);
CONTEXT_GUARD_DUMP=<файл> пишет вход хука. Состояние прежнего knowledge_guard
(~/.claude/knowledge-guard/<session_id>.json) подхватывается, пока своего нет. Разбор — README.md рядом.
Перенос context-guard.py: Node 18+, только встроенные модули; поведение и файлы — как у Python-версии.
`;

class SystemExit {
  // sys.exit Python — исключение: finally в main успевает записать состояние и при отказе кодом 2.
  // code — код выхода или текст (его — в stderr, код 1).
  constructor(code) {
    this.code = code;
  }
}

class EncodeError extends TypeError {} // UnicodeEncodeError Python: не OSError, мимо except OSError

let ss;
try {
  ss = await import("./session-state.mjs");
} catch { // набор повреждён: хук молча пропускает работу, падать нельзя
  process.exit(0);
}
// Имени нет (модуль другой версии) — как ImportError у Python: тот же тихий выход, а не undefined дальше.
const gone = () => process.exit(0);
const { claude_dir = gone(), cleanup_status = gone(), emit_context = gone(), emit_json = gone(),
  entries_backwards = gone(), note_compact = gone(), parse_tokens = gone(), read_input = gone(), safe_sid = gone(),
  session_state = gone(), status_dir = gone(), write_json = gone(), PY_S = gone(), _ascii_digits = gone(),
  _floordiv = gone(), _get = gone(), _home = gone(), _is_dict = gone(), _mtime = gone(), _or = gone(),
  _py_int = gone(), _py_str = gone(), _py_str_repr = gone(), _sleep = gone(), _time = gone(), _truthy = gone(),
  _well_formed = gone(), _write_all = gone() } = ss;

// --- Python-семантика ----------------------------------------------------------------------------
// re Python: \s, \w и \b юникодные, у RegExp — нет. \s — PY_S, \w — буквы, цифры и «_» (сверено со
// всеми символами Unicode Python), \b после буквы — «дальше не буква», ^ при re.M — (?:^|(?<=\n)),
// «.» — [^\n], \d — \p{Nd}.
const S = `[${PY_S}]`;
const W = "\\p{L}\\p{N}_";
const WORD_END = `(?![${W}])`;
const PY_STRIP = new RegExp(`^${S}+|${S}+$`, "gu");
const PY_LSTRIP = new RegExp(`^${S}+`, "u");
const PY_SPLIT = new RegExp(`${S}+`, "u");
// float() Python после замены цифр других письменностей: «_» только между цифрами, inf, infinity, nan.
const PY_FLOAT = /^[+-]?(?:(?:(?:\d(?:_?\d)*)?\.\d(?:_?\d)*|\d(?:_?\d)*\.?)(?:[eE][+-]?\d(?:_?\d)*)?|inf(?:inity)?|nan)$/i;

function _strip(s) {
  return s.replace(PY_STRIP, "");
}

function _decimal_ascii(s) {
  // Как float() и int() Python: цифры любой письменности — ASCII, прочее не-ASCII — заведомый мусор.
  let out = "";
  for (const ch of s) out += ch.codePointAt(0) < 0x80 ? ch : /\p{Nd}/u.test(ch) ? _ascii_digits(ch) : "?";
  return out;
}

function _py_float(s) {
  // float(s) Python или null (ValueError).
  const t = _decimal_ascii(_strip(s));
  if (!PY_FLOAT.test(t)) return null;
  const body = t.replace(/^[+-]/, "").toLowerCase();
  if (body === "nan") return NaN;
  if (body.startsWith("inf")) return t[0] === "-" ? -Infinity : Infinity;
  return Number(t.replace(/_/g, ""));
}

function _py_eq(a, b) {
  // == Python для значений состояния: 1 == 1.0 == True; списки и словари хук не меняет — по ссылке.
  const num = (x) => typeof x === "number" || typeof x === "boolean";
  return num(a) && num(b) ? Number(a) === Number(b) : a === b;
}

function _same(a, b) {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && _py_eq(a[key], b[key]));
}

function _or_dict(x) {
  // (x or {}) перед .get(): правдивое не-словарь у Python — AttributeError.
  if (!_truthy(x)) return {};
  if (!_is_dict(x)) throw new TypeError("object has no attribute 'get'");
  return x;
}

function _get_or(obj, key, def) {
  // obj.get(key, def) Python: ключ со значением null — null, а не def.
  return Object.hasOwn(obj, key) ? obj[key] : def;
}

function _text(b) {
  // b.get("text", "") там, где Python ждёт строку (join, re.search): иначе TypeError.
  const text = _get_or(b, "text", "");
  if (typeof text !== "string") throw new TypeError("expected string");
  return text;
}

function _iter(x) {
  // for … in x Python ради словарей-блоков: у строки (символы) и словаря (ключи) их нет, число — TypeError.
  if (Array.isArray(x)) return x;
  if (typeof x === "string" || _is_dict(x)) return [];
  throw new TypeError("object is not iterable");
}

function _utf8(text) {
  // str.encode("utf-8"): одиночный суррогат — UnicodeEncodeError.
  if (!_well_formed(text)) throw new EncodeError("surrogates not allowed");
  return Buffer.from(text, "utf8");
}

function _text_mode(text) {
  // Текстовый режим Python на Windows: "\n" при записи становится "\r\n".
  return process.platform === "win32" ? text.replace(/\n/g, "\r\n") : text;
}

function _env(...names) {
  for (const name of names) {
    const value = _strip(process.env[name] ?? "");
    if (value) return value;
  }
  return null;
}

function _tokens_env(def, ...names) {
  // Токены из переменной: «300000» — как есть, «300k», «0.3m» — по parse_tokens; мусор — def.
  const raw = _env(...names);
  if (raw === null) return def;
  const n = /^\p{Nd}+$/u.test(raw) ? Number(_decimal_ascii(raw)) : parse_tokens(raw);
  return n === null ? def : n;
}

function _float_env(def, ...names) {
  const raw = _env(...names);
  const value = raw === null ? null : _py_float(raw);
  return value === null ? def : value;
}

const BASE = _tokens_env(300_000, "CONTEXT_GUARD_BASE");
const HARD = _tokens_env(900_000, "CONTEXT_GUARD_HARD");
// Отсрочка — не дальше этого шага от текущего контекста: модели охотно откладывают на «+150k» по инерции.
const STEP = _tokens_env(150_000, "CONTEXT_GUARD_STEP");
const COMPACT_SLACK = 10; // с: сжатие без отметки PreCompact считать начатым настолько раньше SessionStart
const AUTO_CEILING = _float_env(0.92, "CONTEXT_GUARD_AUTO_CEILING");
const MIN_UNSORTED = _tokens_env(20_000, "CONTEXT_GUARD_MIN_UNSORTED", "KNOWLEDGE_GUARD_MIN_UNSORTED");
// Предел переходной `echo knowledge-keep-context` сверх HARD_eff; не задан — только HARD_eff.
const KEEP_MAX = _tokens_env(null, "CONTEXT_GUARD_KEEP_MAX", "KNOWLEDGE_GUARD_KEEP_MAX");
const REWAKE_DELAY = _float_env(2.0, "CONTEXT_GUARD_REWAKE_DELAY", "KNOWLEDGE_GUARD_REWAKE_DELAY");
const AUTO_RESERVE = 40_000; // без панели: столько токенов до края окна оставить самому сжатию
// Отказов автосжатию подряд, дальше оно проходит: с autoCompactWindow 500k при окне 1M отказы иначе
// шли бы до потолка 920k.
const AUTO_MAX_BLOCKS = 40;
const PENDING_TTL = 30 * 60; // раскладка агенту в панель herdr — не чаще
const PENDING_REPEAT = 4; // пока сжатие отложено, напоминание — на первом и каждом 4-м вызове инструмента
const STATE_TTL_DAYS = 30;

const SKILL = "/session-extract-knowledge";
const SKILL_TEXT = "навык session-extract-knowledge";
const MARKER = "knowledge-sorted";
const KEEP = "knowledge-keep-context";
const QUOTES = "весь сигнал — в двойных кавычках, внутри без `\"`, `$` и обратных кавычек: иначе `;` и `&&` в тексте режут команду";
const DONE = `В конце выполни отдельной командой Bash \`echo ${MARKER}\` — это отметка для хука.`;
const EXTRACT_SKILLS = ["session-extract-knowledge", "session-end"];
// Навык раскладки — с префиксом плагина или без: «/session-end», «/<плагин>:session-end» (переходный срок,
// пока на машине жив прежний плагин с этими навыками).
const EXTRACT_PROMPT = new RegExp(`^${S}*/(?:[${W}\\-]+:)?(?:${EXTRACT_SKILLS.join("|")})${WORD_END}`, "u");
const EXTRACT_COMMAND = new RegExp(`<command-name>/(?:[${W}\\-]+:)?(?:${EXTRACT_SKILLS.join("|")})</command-name>`, "u");
const MARKER_CMD = new RegExp(`(?:^${S}*|[;&|(]${S}*)echo${S}+["']?${MARKER}${WORD_END}`, "u");
const KEEP_CMD = new RegExp(`(?:^${S}*|[;&|(]${S}*)echo${S}+["']?${KEEP}${S}+([\\p{Nd}.]+${S}*[kKmM]?)([^\\n]*)`, "u");
// Только строка-цитата в начале строки: пример пометки внутри текста ответа (в `…`, в списке) — не отсрочка.
const DEFER_NOTE = new RegExp(
  `(?:^|(?<=\\n))>${S}*контекст[^\\n]*?проверка отложена до${S}*(\\p{Nd}+(?:[.,]\\p{Nd}+)?${S}*[kKкКmMмМ]?)`, "u");

// --- состояние ---------------------------------------------------------------------------------

function state_path(sid) {
  return path.join(claude_dir(), "context-guard", `${safe_sid(sid)}.json`);
}

function prompt_path(sid) {
  return path.join(claude_dir(), "context-guard", `${safe_sid(sid)}.prompt.md`);
}

function fresh_state() {
  return { baseline: null, sorted_at: null, tokens: null, next: null, pending: null,
    pending_at: 0, pending_hits: 0, rewoken_at: 0, custom_instructions: null,
    manual_blocked: false, auto_blocks: 0, compacted_unsorted: null,
    extract_pending_at: null, compact_started_at: null };
}

function compact_mod(sid) {
  // Мод context-compact в этой сессии сжимает сам (его файл-признак, `ok: true`).
  const data = _read(path.join(claude_dir(), "context-guard", `${safe_sid(sid)}.compact-mod`));
  return _truthy(data) && _get(data, "ok") === true;
}

const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }); // read_text("utf-8") BOM не снимает

function _read(p) {
  let data;
  try {
    data = JSON.parse(UTF8.decode(fs.readFileSync(p)));
  } catch {
    return null;
  }
  return _is_dict(data) ? data : null;
}

function read_state(sid) {
  // Состояние сессии или null. Своего нет — берётся состояние прежнего knowledge_guard: переход без
  // волны напоминаний. Прежний файл context-guard без baseline получает его на первом замере.
  let data = _read(state_path(sid));
  if (data === null) {
    data = _read(path.join(claude_dir(), "knowledge-guard", `${safe_sid(sid)}.json`));
    if (data !== null && _truthy(_get(data, "keep_until")) && !_truthy(_get(data, "next"))) {
      data.next = data.keep_until; // отсрочка автосжатия knowledge_guard — теперь отсрочка проверки
    }
  }
  if (data === null) return null;
  const st = { ...fresh_state(), ...data };
  if (!["manual", "auto"].includes(st.pending)) {
    st.pending = null; // «review» knowledge_guard: отсрочки теперь — порог next
  }
  return st;
}

function load(sid) {
  let st = read_state(sid);
  if (st === null) {
    // Хук поставлен посреди идущей сессии: всё, что в ней было, считаем неразложенным.
    st = fresh_state();
    st.baseline = 0;
  }
  return st;
}

function save(sid, st) {
  write_json(state_path(sid), st);
}

function cleanup() {
  const cutoff = _time() - STATE_TTL_DAYS * 86400;
  for (const name of ["context-guard", "knowledge-guard"]) {
    const dir = path.join(claude_dir(), name);
    let files;
    try {
      files = fs.readdirSync(dir);
    } catch {
      continue;
    }
    for (const f of files) {
      const p = path.join(dir, f);
      try {
        if (fs.statSync(p).isFile() && _mtime(p) < cutoff) fs.unlinkSync(p);
      } catch {
        // занят, исчез — пропуск
      }
    }
  }
  cleanup_status();
}

// --- замер и пороги ----------------------------------------------------------------------------

function measure(inp = null, sid = null) {
  // Замер контекста (session-state) и пороги этой сессии: hard — HARD_eff, base — BASE_eff.
  const s = session_state(inp, { fields: ["model", "context"], sid });
  const auto = _get(s, "auto_compact_at"); // null — автосжатие выключено
  const window = _get(s, "window") || 1_000_000;
  // Отсрочка дальше порога автосжатия бессмысленна: там всё равно сработает PreCompact. Автосжатие
  // выключено — предел у потолка окна: у 200k-модели HARD (900k) не наступил бы никогда.
  const hard = Math.min(HARD, auto || _py_int(AUTO_CEILING * window));
  // При пороге автосжатия ниже BASE (200k-модели, autoCompactWindow < 333k) вопрос иначе не задавался бы
  // никогда: окно 1M с autoCompactWindow 500k — 467k/300k, 200k-модель — 167k/100k.
  const base = Math.min(BASE, Math.max(hard - STEP, _floordiv(hard * 6, 10)));
  return { tokens: _get(s, "tokens"), window, after_compact: _truthy(_get(s, "after_compact")), hard, base };
}

function unsorted(st, cur) {
  return Math.max(0, cur - Math.max(st.baseline || 0, st.sorted_at || 0));
}

function note_baseline(st, m) {
  // Первый замер цикла — база: что было до него (после сжатия — сводка), разложено.
  if (st.baseline === null && !m.after_compact) st.baseline = m.tokens;
}

function write_mark(sid, st, m) {
  // Отметка для статусной строки: на каком размере контекста следующая проверка. Файл необязательный:
  // claude-statusline показывает «⚑650k», только если он есть, и о context-guard больше ничего не знает.
  // Порог, на котором спросит хук; если он уже пройден (проверка идёт сейчас) — тот же порог, а не предел.
  const mark = _or(st.next, m.base);
  write_json(path.join(status_dir(), `${safe_sid(sid)}.ctx-mark`), { at: _py_int(Math.min(mark, m.hard)) });
}

function set_next(st, n, hard, step = true) {
  // Отложить проверку до n токенов: круглым числом (до 10k вверх: «410k», а не «401829»), не дальше
  // hard, с step — и не дальше STEP от текущего. null — n не больше текущего контекста.
  const cur = _or(st.tokens, 0);
  if (n <= cur) return null;
  const top = step ? Math.min(n, cur + STEP) : n;
  st.next = Math.min(hard, -_floordiv(-top, 10_000) * 10_000);
  return st.next;
}

// int(str) Python: пробелы по краям (ASCII \x1c-\x1f — нет), знак, «_» только между цифрами.
const INT_S = "[\\t-\\r \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]";
const PY_INT_STR = new RegExp(`^${INT_S}*([+-]?\\p{Nd}(?:_?\\p{Nd})*)${INT_S}*$`, "u");

function _py_int_big(x) {
  // int(x) Python без потери точности (BigInt): bool, число и строка цифр; прочее — ошибка.
  if (typeof x === "string") {
    const m = PY_INT_STR.exec(x);
    if (!m) throw new RangeError(`invalid literal for int() with base 10: ${_py_str_repr(x)}`);
    return BigInt(_ascii_digits(m[1]).replace(/_/g, ""));
  }
  if (typeof x !== "number" && typeof x !== "boolean") throw new TypeError("int() argument must be a string or a number");
  return BigInt(_py_int(x));
}

function k(n) {
  // f"{int(n) // 1000}k": // — с округлением вниз и для отрицательных.
  const i = _py_int_big(n);
  return `${i / 1000n - (i % 1000n < 0n ? 1n : 0n)}k`;
}

// --- транскрипт --------------------------------------------------------------------------------

function is_boundary(e) {
  return _get(e, "type") === "system" && _get(e, "subtype") === "compact_boundary";
}

function is_extraction(e) {
  const content = _get(_or_dict(_get(e, "message")), "content");
  if (_get(e, "type") === "assistant" && Array.isArray(content)) {
    for (const b of content) {
      if (_is_dict(b) && _get(b, "type") === "tool_use" && _get(b, "name") === "Skill"
        && EXTRACT_SKILLS.includes(_py_str(_get_or(_or_dict(_get(b, "input")), "skill", "")).split(":").at(-1))) {
        return true;
      }
    }
    return false;
  }
  if (_get(e, "type") === "user" && typeof content === "string") return EXTRACT_COMMAND.test(content);
  return false;
}

function is_user_prompt(e) {
  if (_get(e, "type") !== "user" || _truthy(_get(e, "isMeta")) || _truthy(_get(e, "isSidechain"))
    || _truthy(_get(e, "isCompactSummary"))) {
    return false;
  }
  let content = _get(_or_dict(_get(e, "message")), "content");
  if (Array.isArray(content)) {
    if (content.some((b) => _is_dict(b) && _get(b, "type") === "tool_result")) return false;
    content = content.filter(_is_dict).map(_text).join(" ");
  }
  return typeof content === "string" && Boolean(_strip(content)) && !content.replace(PY_LSTRIP, "").startsWith("<");
}

function is_extraction_running(p) {
  // Раскладка вызвана после последнего сжатия — идёт или закончилась.
  for (const e of entries_backwards(p)) {
    if (is_boundary(e)) return false;
    if (is_extraction(e)) return true;
  }
  return false;
}

function extracted_since_compact(p) {
  // Нечего раскладывать: после последней раскладки промптов пользователя не было, а если раскладки
  // после прошлого сжатия не было — не было промптов и после самого сжатия.
  for (const e of entries_backwards(p)) {
    if (is_boundary(e) || is_extraction(e)) return true;
    if (is_user_prompt(e)) return false; // промпт новее раскладки или сжатия: дальше назад читать незачем
  }
  return false;
}

function deferred_in_last_answer(p) {
  // Порог из пометки «> контекст Nk, проверка отложена до Mk» в ответе на прошлый промпт.
  for (const e of entries_backwards(p)) {
    if (is_user_prompt(e) || is_boundary(e)) return null;
    if (_get(e, "type") !== "assistant" || _truthy(_get(e, "isSidechain"))) continue;
    for (const b of _iter(_or(_get(_or_dict(_get(e, "message")), "content"), []))) {
      if (_is_dict(b) && _get(b, "type") === "text") {
        const m = DEFER_NOTE.exec(_text(b));
        if (m) return parse_tokens(m[1]);
      }
    }
  }
  return null;
}

// --- тексты ------------------------------------------------------------------------------------

function ending(sid, prompt) {
  // Концовка вопроса — как сжать контекст: [что сделать, что будет с запросом после сжатия]. В панели
  // herdr сжимает claude-compact, без неё — мод context-compact; оба присылают запрос заново. Без панели
  // и мода сжать может только владелец.
  const panel = Boolean(process.env.HERDR_PANE_ID);
  const mod = compact_mod(sid);
  if (panel || mod) {
    const p = prompt_path(sid);
    try {
      fs.mkdirSync(path.dirname(p), { recursive: true });
      // Как Path.write_text: open("w") обрезает файл ещё до кодирования, а одиночный суррогат в промпте —
      // UnicodeEncodeError: он, в отличие от OSError, роняет обработчик.
      const bad = !_well_formed(prompt);
      fs.writeFileSync(p, bad ? "" : Buffer.from(_text_mode(prompt), "utf8"));
      if (bad) throw new EncodeError("surrogates not allowed");
    } catch (e) {
      if (e instanceof EncodeError) throw e;
    }
    const call = panel ? `вызови \`~/.claude/bin/claude-compact --resend ${p}\`` :
      `выполни отдельной командой Bash \`echo "context-compact --resend ${p} -- <инструкция>"\` `
      + `(сожмёт мод после хода; ${QUOTES})`;
    return [`к запросу не приступай: вызови скилл ${SKILL} (отчёт — одной строкой) и в конце ${call}, а в `
      + "инструкции для /compact укажи, какая работа шла и что осталось незакрытым. После сжатия этот "
      + "запрос придёт снова, и ты выполнишь его уже в сжатом контексте", ", а запрос придёт снова"];
  }
  return [`вызови скилл ${SKILL} (отчёт — одной строкой), затем выполни запрос, а последней строкой ответа, `
    + "попроси владельца набрать `/compact <инструкция>` — инструкция "
    + "одной строкой до 250 символов: какая работа шла и что осталось незакрытым", ""];
}

function question(sid, prompt, cur, m) {
  const [extract, after] = ending(sid, prompt);
  const hard = m.hard;
  if (cur >= hard) {
    return `Проверка контекста (context-guard): в контексте ${k(cur)} токенов — это предел ${k(hard)}, `
      + `откладывать нельзя. Прежде чем выполнять этот запрос, ${extract}.`;
  }
  return `Проверка контекста (context-guard): в контексте ${k(cur)} токенов. По умолчанию — сжать: ${extract}. `
    + "Большая часть такого контекста — вывод команд, прочитанные файлы и обсуждение прошлых задач; после "
    + `сжатия остаются сводка с текущей задачей и её состоянием, пути к разложенным файлам${after}. Не `
    + "сжимать можно только по конкретной причине: этот запрос — продолжение текущего шага, и ему "
    + "нужны подробности, которые не переживут сводку и которых нет в файлах (точный вывод, несохранённое "
    + "состояние, середина правки). «Задача ещё идёт» сама по себе не причина. Если причина есть — выполняй "
    + "запрос и последней строкой ответа (после метки времени, если пишешь её) допиши строку-цитату «> контекст "
    + `${k(cur)}, проверка отложена до <порог>k: <причина в 3–6 словах>», порог — не дальше `
    + `${k(Math.min(hard, cur + STEP))} (хук дальше не пустит); бери ровно столько, сколько нужно на этот шаг, `
    + `например «до ${k(Math.min(hard, cur + 50_000))}». Пометка — только в ответе на этот промпт, в следующих `
    + `её не повторяй: хук спросит снова сам. На ${k(hard)} отложить будет нельзя.`;
}

function compact_waits(cur, sid) {
  // Без панели знания уже разложены, а сжатия ещё не было: раскладку не повторять.
  const how = compact_mod(sid)
    ? "последней командой хода выполни отдельной командой Bash `echo \"context-compact -- <инструкция>\"` "
      + `(сожмёт мод после хода; ${QUOTES})`
    : "последней строкой ответа напомни владельцу набрать `/compact <инструкция>`";
  return `Проверка контекста (context-guard): в контексте ${k(cur)} токенов, знания уже разложены — сжатия `
    + `ещё не было. Раскладку не повторяй: выполни запрос и ${how} — инструкция одной строкой до `
    + "250 символов: какая работа шла и что осталось незакрытым.";
}

function pending_text(st, cur, window, sid = null) {
  // Напоминание, пока сжатие отложено (без панели): ручное — до повторного /compact, авто — до отметки.
  if (st.pending === "manual") {
    let again = "/compact";
    if (_truthy(st.custom_instructions)) again += ` ${_py_str(st.custom_instructions)}`;
    if (_truthy(sid) && compact_mod(sid)) {
      const arg = _truthy(st.custom_instructions) ? ` -- ${_py_str(st.custom_instructions)}` : " -- <инструкция>";
      return "context-guard: владелец вызвал /compact, хук отложил сжатие — сначала разложи "
        + `знания этой сессии по полочкам (${SKILL_TEXT}, полный режим). ${DONE} Потом ответь `
        + "владельцу одной строкой, что куда записано, и последней командой хода выполни "
        + `отдельной командой Bash \`echo "context-compact${arg}"\` (${QUOTES}) — мод повторит /compact сам.`;
    }
    return "context-guard: владелец вызвал /compact, хук отложил сжатие — сначала разложи "
      + `знания этой сессии по полочкам (${SKILL_TEXT}, полный режим). ${DONE} Потом ответь `
      + `владельцу одной строкой: что куда записано и «можно ${again}» (повторный `
      + "/compact пройдёт в любом случае).";
  }
  return "context-guard: автосжатие контекста отложено — знания с последнего выноса не "
    + `записаны (~${k(unsorted(st, cur))}). Запас до края окна ~${k(Math.max(0, window - cur))}, сжатию `
    + `нужно ~${k(AUTO_RESERVE)}. Прямо сейчас, коротко: только незаписанное — решения, замеры, `
    + `баги, грабли, открытые хвосты текущей задачи (${SKILL_TEXT}, короткий режим); большие файлы `
    + `целиком не читать. ${DONE} Потом продолжай прерванную работу.`;
}

// --- события -----------------------------------------------------------------------------------

function on_prompt(inp, st, sid) {
  let prompt = _get(inp, "prompt");
  prompt = typeof prompt === "string" ? prompt : "";
  if (EXTRACT_PROMPT.test(prompt)) return;
  const m = measure(inp);
  const cur = m.tokens;
  if (cur === null) return;
  note_baseline(st, m);
  st.tokens = cur;
  let text = null;
  if (st.pending) {
    // Сжатие отложено и ждёт раскладки (без панели): напоминание вместо вопроса — просят о том же.
    st.pending_hits += 1;
    text = pending_text(st, cur, m.window, sid);
  }
  if (cur < m.base) {
    // Отложенный порог и постановка раскладки — с нуля. Файл с промптом не удалять: по нему
    // claude-compact --resend присылает запрос заново, и модель читает файл уже после этого хука.
    Object.assign(st, { next: null, extract_pending_at: null });
    write_mark(sid, st, m);
  } else {
    // Отсрочка — пометкой в прошлом ответе: вызывать `defer` не нужно (в режиме auto классификатор
    // блокирует его как самомодификацию, проверено 29.09.2026).
    const n = deferred_in_last_answer(_get(inp, "transcript_path"));
    if (n && n > (st.next || 0)) set_next(st, n, m.hard);
    write_mark(sid, st, m);
    if (text === null && (cur >= m.hard || cur >= (st.next || m.base))) {
      if (!process.env.HERDR_PANE_ID && _truthy(st.sorted_at) && unsorted(st, cur) <= MIN_UNSORTED) {
        text = compact_waits(cur, sid);
      } else {
        text = question(sid, prompt, cur, m);
      }
    }
  }
  if (text) emit_context("UserPromptSubmit", text);
}

function on_tool(inp, st, sid) {
  const tool_input = _get(inp, "tool_input");
  let command = _is_dict(tool_input) ? _get(tool_input, "command") : null;
  command = typeof command === "string" ? command : "";
  const keep = KEEP_CMD.exec(command);
  const marker = !keep && MARKER_CMD.test(command);
  // Замер — только когда он нужен: хук висит на каждом вызове инструмента.
  if (!(keep || marker || st.pending || st.baseline === null)) return;
  const m = measure(inp);
  const cur = m.tokens;
  if (cur === null) return;
  note_baseline(st, m);
  st.tokens = cur;
  if (keep) {
    emit_context("PostToolUse", request_keep(st, sid, m, keep));
  } else if (marker) {
    const was = st.pending;
    Object.assign(st, { sorted_at: cur, pending: null, pending_hits: 0, manual_blocked: false, auto_blocks: 0 });
    if (was === "auto") {
      emit_context("PostToolUse", "context-guard: отметка принята, автосжатие пройдёт на следующем "
        + "шаге. Продолжай прерванную работу.");
    }
  } else if (st.pending) {
    st.pending_hits += 1;
    if (((st.pending_hits % PENDING_REPEAT) + PENDING_REPEAT) % PENDING_REPEAT === 1) {
      emit_context("PostToolUse", pending_text(st, cur, m.window, sid));
    }
  }
}

function request_keep(st, sid, m, match) {
  // Переходное: `echo knowledge-keep-context <N> <зачем>` прежнего knowledge_guard — отсрочка проверки
  // до N, как цитатой, только без шага STEP.
  const cur = st.tokens;
  const limit = KEEP_MAX ? Math.min(m.hard, KEEP_MAX) : m.hard;
  const target = parse_tokens(match[1]);
  if (cur >= limit) {
    return `context-guard: отсрочек больше нет — пройден предел ${k(limit)}. Разложи незаписанное `
      + `(${SKILL_TEXT}) и выполни \`echo ${MARKER}\`.`;
  }
  if (!target || target <= cur) {
    return `context-guard: отсрочка не принята — нужна граница выше текущих ${k(cur)}, например `
      + `\`echo ${KEEP} ${k(Math.min(cur + STEP, limit))} <зачем>\`.`;
  }
  const granted = set_next(st, target, limit, false);
  write_mark(sid, st, m);
  const cut = granted < target ? ` (урезано до предела ${k(limit)})` : "";
  return `context-guard: отсрочка принята — следующая проверка контекста на ${k(granted)}${cut}, там `
    + "спрошу снова. Дальше откладывай цитатой «> контекст Nk, проверка отложена до Mk: <зачем>» в "
    + "самом конце ответа. Продолжай работу.";
}

function passed(st, left, fresh) {
  // Сжатие проходит: отложенное снято; без раскладки — запомнить, сколько не разложено (SessionStart).
  Object.assign(st, { pending: null, manual_blocked: false, auto_blocks: 0, compacted_unsorted: fresh ? null : left,
    compact_started_at: _time() });
}

function on_precompact(inp, st, sid) {
  const m = measure(inp);
  if (m.tokens === null) return; // без замера сжатию не мешать
  const left = unsorted(st, m.tokens);
  if (process.env.HERDR_PANE_ID) precompact_herdr(inp, st, m, left);
  else precompact_plain(inp, st, m, left);
}

function precompact_herdr(inp, st, m, left) {
  // Панель herdr: раскладку ставим агенту в очередь, сжатие он вызовет сам (claude-compact).
  const p = _get(inp, "transcript_path");
  const extracted = extracted_since_compact(p);
  const fresh = left <= MIN_UNSORTED || extracted;
  if (fresh) st.extract_pending_at = null;
  if (_get(inp, "trigger") === "auto") {
    // Автосжатие ниже потолка отменяем: либо раскладка ещё нужна, либо она уже идёт или
    // закончилась, и тогда сожмёт claude-compact (иначе сжатий будет два, проверено 29.09.2026).
    // Claude Code повторяет попытку на каждом запросе; у потолка и после AUTO_MAX_BLOCKS отказов
    // пропускаем, чтобы сессия не упала.
    if (m.tokens >= AUTO_CEILING * m.window || st.auto_blocks >= AUTO_MAX_BLOCKS) {
      passed(st, left, fresh);
      return;
    }
    if (extracted || is_extraction_running(p)) {
      st.auto_blocks += 1;
      emit_json({ decision: "block", reason: "Знания раскладываются, контекст сожмётся после "
        + "раскладки (context-guard)." });
      return;
    }
    if (fresh) {
      // Свежа только отметка `echo knowledge-sorted` без навыка: claude-compact никто не позовёт,
      // и отказ тянулся бы до AUTO_MAX_BLOCKS — знания разложены, сжатие проходит.
      passed(st, left, true);
      return;
    }
    st.auto_blocks += 1;
  } else if (fresh) {
    passed(st, left, fresh);
    return;
  }
  // Раскладку ставим в очередь один раз: при повторных попытках автосжатия только отменяем.
  if (_time() - (st.extract_pending_at || 0) > PENDING_TTL) {
    st.extract_pending_at = _time();
    let custom = _get(inp, "custom_instructions");
    custom = typeof custom === "string" ? custom.split(PY_SPLIT).filter(Boolean).join(" ") : "";
    const msg = `${SKILL} Перед сжатием контекста (context-guard): разложи знания, разбирая только то, что `
      + "появилось после прошлого сжатия, и в конце вызови ~/.claude/bin/claude-compact"
      + (custom ? ` -- ${custom}` : "") + ".";
    queue_extraction(process.env.HERDR_PANE_ID, msg);
  }
  emit_json({ decision: "block", reason: "Сначала раскладываю знания по местам, потом контекст "
    + "сожмётся сам (context-guard)." });
}

function _which(cmd) {
  // shutil.which Python 3.11 на POSIX: первый в PATH существующий исполняемый не-каталог; PATH не задан —
  // CS_PATH системы, пустой — null. Путь склеивается как os.path.join, без нормализации.
  let dirs = process.env.PATH;
  if (dirs === undefined) dirs = process.platform === "darwin" ? "/usr/bin:/bin:/usr/sbin:/sbin" : "/bin:/usr/bin";
  if (!dirs) return null;
  const seen = new Set();
  for (const dir of dirs.split(path.delimiter)) {
    if (seen.has(dir)) continue;
    seen.add(dir);
    const name = process.platform === "win32" ? path.join(dir, cmd)
      : dir === "" || dir.endsWith("/") ? dir + cmd : `${dir}/${cmd}`;
    try {
      if (!fs.statSync(name).isDirectory()) {
        fs.accessSync(name, fs.constants.X_OK);
        return name;
      }
    } catch {
      // нет файла или не исполняемый
    }
  }
  return null;
}

function _executable(p) {
  // os.access(p, os.X_OK): ошибка — false.
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Аргументы процесса Python кодирует с surrogateescape: одиночные \udc80-\udcff — сырые байты (Node
// передаст U+FFFD), прочий одиночный суррогат — ValueError, и процесс не запускается.
const UNENCODABLE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDC7F\uDD00-\uDFFF]/;

function queue_extraction(pane, msg) {
  // Промпт агенту в панель herdr после конца хода: процесс отвязан (свой сеанс, без каналов хука) и
  // переживает хук. Шлёт через claude-prompt слоя terminal, если он есть: тот прячет недописанный
  // черновик владельца, иначе промпт дописался бы в его конец.
  const herdr = _which("herdr") || path.join(_home(), ".local", "bin", "herdr");
  let send = path.join(_home(), ".claude", "bin", "claude-prompt");
  send = _executable(send) ? [send] : [herdr, "agent", "prompt"];
  const args = ["-c", 'sleep 1; "$0" agent wait "$1" --until idle --until done --timeout 1800000 >/dev/null 2>&1 '
    + '&& shift && "$@" >/dev/null 2>&1', herdr, pane, ...send, pane, msg];
  if (args.some((a) => UNENCODABLE.test(a))) return; // ValueError у Python: аргумент не кодируется
  try {
    const child = spawn("/bin/sh", args, { stdio: "ignore", detached: true, windowsHide: true });
    child.on("error", () => {}); // нет /bin/sh (Windows) — как OSError у Python, молча
    child.unref();
  } catch {
    // как (OSError, ValueError) у Python — молча
  }
}

function precompact_plain(inp, st, m, left) {
  // Без панели: сжать может только владелец — отказ кодом 2, модели — напоминания до отметки.
  if (left <= MIN_UNSORTED) {
    passed(st, left, true);
    return;
  }
  if (_get(inp, "trigger") === "auto") {
    // Для автосжатия свежая раскладка — только отметка: вызов навыка в транскрипте значит и «раскладка
    // ещё идёт», а сжатие посреди неё сотрёт несделанное (в панели herdr такое автосжатие тоже ждёт).
    // У края окна и после AUTO_MAX_BLOCKS отказов — пропустить, чтобы сессия не упала.
    if (m.window - m.tokens < AUTO_RESERVE || st.auto_blocks >= AUTO_MAX_BLOCKS) {
      passed(st, left, false);
      return;
    }
    if (st.pending !== "auto") Object.assign(st, { pending: "auto", pending_at: _time(), pending_hits: 0 });
    st.auto_blocks += 1;
    block_stderr("context-guard: автосжатие отложено до выноса знаний.");
  }
  if (extracted_since_compact(_get(inp, "transcript_path"))) {
    passed(st, left, true);
    return;
  }
  if (_truthy(st.manual_blocked)) { // владелец повторил /compact — не держим
    passed(st, left, false);
    return;
  }
  const custom = _get(inp, "custom_instructions");
  Object.assign(st, { pending: "manual", pending_at: _time(), pending_hits: 0, manual_blocked: true,
    custom_instructions: typeof custom === "string" && _strip(custom) ? custom : null });
  block_stderr("context-guard: сжатие отложено — сначала Claude разложит знания сессии по полочкам "
    + "(навык session-extract-knowledge) и напишет «жми /compact». Если он не начал сам, "
    + "отправьте любое сообщение. Повторный /compact пройдёт в любом случае.");
}

function block_stderr(text) {
  // Как block_stderr из session-state.mjs (причина в stderr байтами UTF-8, код 2), но выход — исключением
  // SystemExit, как sys.exit у Python: иначе finally в main не записал бы состояние.
  try {
    if (typeof text === "string" && _well_formed(text)) _write_all(2, Buffer.from(text, "utf8"));
  } catch {
    // как в Python — молча
  }
  throw new SystemExit(2);
}

function on_rewake(sid) {
  // asyncRewake: разбудить модель после отложенного /compact (сессия может простаивать).
  // Дать основному обработчику PreCompact записать состояние; time.sleep: max(0.0, nan) — 0, сверх
  // предела _PyTime_t — OverflowError.
  const delay = REWAKE_DELAY > 0 ? REWAKE_DELAY : 0;
  if (delay * 1e9 > 2 ** 63) throw new RangeError("timestamp too large to convert to C _PyTime_t");
  _sleep(delay * 1000);
  const st = read_state(sid);
  if (st && st.pending === "manual" && _time() - st.pending_at < 60 && !_py_eq(st.rewoken_at, st.pending_at)) {
    st.rewoken_at = st.pending_at;
    save(sid, st);
    block_stderr(pending_text(st, 0, 0, sid));
  }
}

function on_session_start(inp, sid) {
  const source = _get(inp, "source");
  if (!["startup", "clear", "compact"].includes(source)) return null;
  const old = read_state(sid);
  const st = fresh_state();
  const left = old && old.compacted_unsorted;
  if (source === "compact") {
    // Без PreCompact (не было замера) — с запасом: сжатие идёт десятки секунд.
    const started = _get(old || {}, "compact_started_at");
    note_compact(sid, _truthy(started) ? started : _time() - COMPACT_SLACK);
  }
  if (source === "compact" && _truthy(left)) {
    emit_context("SessionStart",
      `context-guard: контекст сжат, а ~${k(left)} работы перед сжатием не разложены по `
      + `полочкам. Полная запись сессии цела: ${_py_str(_get(inp, "transcript_path"))} (JSONL, сжатая часть — `
      + "до последней строки с \"compact_boundary\"). В ближайшей удобной точке найди в ней "
      + "grep'ом по ключевым словам решения, замеры, баги и грабли, которых нет в документации, "
      + `и запиши их (${SKILL_TEXT}); целиком запись не читать. ${DONE}`);
  }
  cleanup();
  return st;
}

function say(fd, text) {
  // Строка в stdout/stderr байтами UTF-8: консоль Windows иначе бьёт кириллицу.
  _write_all(fd, _utf8(`${text}\n`));
}

function defer_cli(sid, raw) {
  const n = parse_tokens(raw);
  if (n === null) {
    say(2, `не понял число токенов: ${_py_str_repr(raw)} (пример: 650000 или 650k)`);
    throw new SystemExit(1);
  }
  const st = load(sid);
  const m = measure(null, sid);
  const cur = _or(st.tokens, 0);
  const nxt = set_next(st, n, m.hard);
  if (nxt === null) {
    say(2, `${k(n)} не больше текущего контекста ${k(cur)}: отложить можно только на порог выше`);
    throw new SystemExit(1);
  }
  save(sid, st);
  write_mark(sid, st, m);
  const note = n > nxt ? ` (урезано до ${k(nxt)}: дальше ${k(STEP)} от текущего и выше ${k(m.hard)} нельзя)` : "";
  say(1, `следующая проверка контекста — на ${k(nxt)}${note}`);
}

const HANDLERS = { UserPromptSubmit: on_prompt, PostToolUse: on_tool, PreCompact: on_precompact };

function main(argv) {
  if (argv[0] === "defer" && argv.length === 3) {
    defer_cli(argv[1], argv[2]);
    return;
  }
  if (!(argv.length === 0 || (argv.length === 1 && ["hook", "rewake"].includes(argv[0])))) throw new SystemExit(DOC);
  if ([process.env.CONTEXT_GUARD_OFF, process.env.KNOWLEDGE_GUARD_OFF].includes("1")) return;
  if ((process.env.CLAUDE_CODE_ENTRYPOINT ?? "").startsWith("sdk")) {
    return; // сессии без человека (claude -p, Agent SDK) — не трогаем
  }
  const inp = read_input("CONTEXT_GUARD_DUMP");
  if (!_truthy(inp) || _truthy(_get(inp, "agent_id"))) {
    return; // субагент: у него своё сжатие и свой контекст
  }
  const sid = _get(inp, "session_id");
  if (typeof sid !== "string" || !sid) return;
  const event = _get(inp, "hook_event_name");
  if (argv[0] === "rewake") {
    // В панели herdr модель будит сама постановка раскладки: второе пробуждение лишнее.
    if (event === "PreCompact" && _get(inp, "trigger") === "manual" && !process.env.HERDR_PANE_ID) on_rewake(sid);
    return;
  }
  if (event === "SessionStart") {
    const st = on_session_start(inp, sid);
    if (st !== null) save(sid, st);
    return;
  }
  const handler = typeof event === "string" && Object.hasOwn(HANDLERS, event) ? HANDLERS[event] : null;
  if (handler === null) return;
  const st = load(sid);
  const before = { ...st };
  try {
    handler(inp, st, sid);
  } finally { // и при отказе кодом 2 (block_stderr) состояние записано
    if (!_same(st, before)) save(sid, st);
  }
}

let code = 0;
try {
  main(process.argv.slice(2));
} catch (e) { // без данных или на странном вводе хук молча пропускает работу
  if (e instanceof SystemExit) {
    code = e.code;
    if (typeof code === "string") { // sys.exit(текст): текст в stderr, код 1
      try {
        _write_all(2, Buffer.from(_text_mode(`${code}\n`), "utf8"));
      } catch {
        // stderr закрыт
      }
      code = 1;
    }
  }
}
process.exit(code);
