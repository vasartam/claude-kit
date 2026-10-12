#!/usr/bin/env python3
"""Состояние сессии для хуков набора: модель, effort, ultracode, контекст, лимиты, дневная норма.

Один модуль на три хука (context-guard, model-advisor, limit-guard). Каждое поле — из самого
свежего источника, с пометкой, откуда оно взято (`src`). Источники:
  мод session-state  — <claude>/session-status/<sid>.state.json (обе оболочки, mods/session-state);
  статусная строка   — <claude>/session-status/<sid>.json, rate-limits.json, week-budget.json
                       (только терминал, terminal/bin/claude-statusline);
  транскрипт сессии  — transcript_path из входа хука (JSONL, читается с конца).
<claude> — CLAUDE_CONFIG_DIR или ~/.claude.

Части считаются лениво: session_state(inp, fields=…) трогает только нужные источники — хуки
висят на каждом PreToolUse/PostToolUse с таймаутом 10 с. Функции не бросают исключений: чего
нет — None.

Здесь же общий ввод-вывод хуков: read_input, emit_context, emit_json, write_json (атомарно),
block_stderr, dump_input. Python 3.9+, только стандартная библиотека, без внешних команд.

Отладка: python3 hooks/session_state.py <session_id> [transcript.jsonl] — результат JSON с src.
"""

import datetime
import json
import os
import re
import sys
import time
from pathlib import Path

FIELDS_ALL = ("model", "effort", "ultracode", "context", "limits", "day")
# Порог автосжатия без мода: min(autoCompactWindow или окно, окно) − резерв (20k вывод + 13k буфер).
# Сходится с замерами: окно 1M → 967k (preTokens границы сжатия), autoCompactWindow 500k → 467k.
# Для окон < 1M (200k → 167k) не замерено.
AUTOCOMPACT_RESERVE = 33_000
# Показания ближе этого (с) — ничья, и тогда мод > статусная строка > транскрипт.
TIE = 2.0
PRIORITY = {"mod": 3, "statusline": 2, "transcript": 1}
LIMITS_MAX_AGE = 8 * 86400  # файлы мода старше — в лимитах не участвуют
STATUS_TTL_DAYS = 30
DAY = 86400
WEEK = 7 * DAY
TAIL_MAX = 64 << 20  # транскрипт с конца читается не дальше
ULTRA_CHUNK = 8 << 20
WINDOWS = ("five_hour", "seven_day")
CONTEXT_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
NATIVE_1M = re.compile(r"fable|sonnet-(?:[5-9]|\d\d)|opus-(?:4-[7-9]|[5-9]|\d\d)|\[1m\]", re.I)
# Ultracode — последнее из ответа команды /effort («Ultracode on/off») и вложений
# ultra_effort_enter / ultra_effort_exit; то же правило, что в claude-statusline.
ULTRA_EVENT = re.compile(rb'"content":\s*"<local-command-stdout>[^"]{0,40}Ultracode (on|off)'
                         rb'|"type":\s*"ultra_effort_(enter|exit)"')
MODEL_IN_TEXT = re.compile(r"exact model ID is (\S+?)\.?(?:\s|$)")
TOKENS_RE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*([kKкКmMмМ]?)\s*")
UNITS = {"k": 1_000, "к": 1_000, "m": 1_000_000, "м": 1_000_000}


# --- пути --------------------------------------------------------------------------------------


def claude_dir():
    """Каталог Claude Code: CLAUDE_CONFIG_DIR или ~/.claude (на Windows Path.home() — USERPROFILE)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env)
    try:
        return Path.home() / ".claude"
    except (RuntimeError, KeyError):
        return Path(os.path.expanduser("~")) / ".claude"


def status_dir():
    return claude_dir() / "session-status"


def safe_sid(sid):
    """session_id для имени файла — как в моде session-state (для UUID совпадает с сырым)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(sid or "unknown"))


# --- ввод-вывод хуков --------------------------------------------------------------------------


def read_input(dump_env=None):
    """Вход хука из stdin: dict или None (пусто, мусор, не объект). dump_env — имя переменной
    с файлом для отладочной записи сырого входа (CONTEXT_GUARD_DUMP, LIMIT_GUARD_DUMP)."""
    try:
        raw = sys.stdin.buffer.read()
    except (OSError, AttributeError, ValueError):
        return None
    if dump_env:
        dump_input(raw, dump_env)
    try:
        inp = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None
    return inp if isinstance(inp, dict) else None


def dump_input(raw, env_name):
    """Дописать сырой вход хука в файл из переменной env_name; переменной нет — ничего."""
    path = os.environ.get(env_name)
    if not path:
        return
    data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    try:
        with open(path, "ab") as f:
            f.write(data + b"\n")
    except OSError:
        pass


def emit_json(obj):
    """JSON в stdout только ASCII: на Windows кириллица иначе бьётся."""
    print(json.dumps(obj, ensure_ascii=True))
    sys.stdout.flush()


def emit_context(event, text):
    emit_json({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def block_stderr(text):
    """Блок с причиной в stderr (байтами UTF-8) и код 2."""
    try:
        sys.stderr.buffer.write(text.encode("utf-8"))
        sys.stderr.flush()
    except (OSError, AttributeError, ValueError):
        pass
    sys.exit(2)


def write_json(path, obj):
    """Атомарная запись: <файл>.tmp.<pid> и os.replace. Хуки одного события идут параллельно,
    на Windows os.replace падает, пока файл открыт читателем, — ошибка тихо пропускается.
    True — записано."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def cleanup_status(days=STATUS_TTL_DAYS):
    """Удалить старые файлы мода, кеш ultracode, отметки context-guard и model-advisor, брошенные tmp."""
    cutoff = time.time() - days * DAY
    for pattern in ("*.state.json", "*.ultra.json", "*.ctx-mark", "*.compact.json", "*.advice.json", "*.tmp.*"):
        try:
            files = list(status_dir().glob(pattern))
        except OSError:
            continue
        for f in files:
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


# --- сжатие -----------------------------------------------------------------------------------


def note_compact(sid, at):
    """Отметка «сжатие прошло, началось в at» — <sid>.compact.json. Пишет context-guard на SessionStart
    после сжатия. Первый промпт после сжатия приходит через доли секунды, а Claude Code дописывает
    транскрипт с задержкой: хук видел последний ответ до сжатия, а не границу (319k вместо 28k,
    03.10.2026). Показания старше отметки _g_context не берёт."""
    if sid:
        write_json(status_dir() / f"{safe_sid(sid)}.compact.json", {"at": at})


def read_compact(sid):
    data = _load_json(status_dir() / f"{safe_sid(sid)}.compact.json") if sid else None
    return _num(data.get("at")) if isinstance(data, dict) else None


# --- мелочи ------------------------------------------------------------------------------------


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _count(x):
    """Целое ≥ 0 (токены, окно) или None."""
    n = _num(x)
    return int(n) if n is not None and 0 <= n < 1e12 else None


def _load_json(path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None


def _iso_ts(value):
    """ISO-время записи транскрипта → секунды эпохи (в 3.9 fromisoformat не знает «Z»)."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _block(data, name):
    block = data.get(name) if isinstance(data, dict) else None
    return block if isinstance(block, dict) else {}


def _at(block, data):
    """Время показания блока мода: его at, иначе ts файла."""
    return _num(block.get("at")) or _num((data or {}).get("ts"))


def _pick(cands):
    """Самое свежее показание (at, src, value); ничья в пределах TIE — по PRIORITY."""
    cands = [c for c in cands if c]
    if not cands:
        return None
    top = max(c[0] or 0 for c in cands)
    return max((c for c in cands if (c[0] or 0) >= top - TIE), key=lambda c: PRIORITY.get(c[1], 0))


def _same_model(a, b):
    norm = lambda m: re.sub(r"\[1m\]$", "", str(m or "")).lower()
    return bool(a) and norm(a) == norm(b)


def parse_tokens(value):
    """900000, «900k», «0.9m», «650к», «0,65М»; строка-число < 5000 без единиц — тысячи
    (модели пишут «100», имея в виду 100k). «auto», пусто и мусор → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 < value < 1e12 else None
    if not isinstance(value, str):
        return None
    m = TOKENS_RE.fullmatch(value)
    if not m:
        return None
    n = float(m.group(1).replace(",", "."))
    mult = UNITS.get(m.group(2).lower()) or (1_000 if n < 5000 else 1)
    n = int(round(n * mult))
    return n if n > 0 else None


# --- окно и порог автосжатия -------------------------------------------------------------------


def _window_default(model):
    override = parse_tokens(os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"))
    if override:
        return override, "env"
    if os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1":
        return 200_000, "env"
    if not model:
        return 1_000_000, "default"  # модель ещё не известна — у владельца по умолчанию окно 1M
    return (1_000_000 if NATIVE_1M.search(str(model)) else 200_000), "default"


def window_for(model):
    """Окно модели без мода и статусной строки: env, затем по имени модели."""
    return _window_default(model)[0]


def settings_value(cwd, key):
    """Значение из настроек: локальные проекта > проекта > пользователя (<claude>/settings.json)."""
    files = []
    if isinstance(cwd, str) and cwd:
        files += [Path(cwd) / ".claude" / "settings.local.json", Path(cwd) / ".claude" / "settings.json"]
    files.append(claude_dir() / "settings.json")
    for f in files:
        data = _load_json(f)
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


def _autocompact(model, cwd, window=None):
    window = window or window_for(model)
    explicit, src = parse_tokens(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")), "env"
    if not explicit:
        explicit, src = parse_tokens(settings_value(cwd, "autoCompactWindow")), "settings"
    if not explicit:
        src = "default"
    return max(1, min(explicit or window, window) - AUTOCOMPACT_RESERVE), src


def autocompact_at(model, cwd=None, window=None):
    """Сколько токенов в контексте, когда Claude Code начнёт автосжатие (если мод его не дал)."""
    return _autocompact(model, cwd, window)[0]


# --- источники ---------------------------------------------------------------------------------


def read_mod(sid):
    """Файл мода session-state <sid>.state.json — dict или None (нет, пусто, битый)."""
    if not sid:
        return None
    data = _load_json(status_dir() / f"{safe_sid(sid)}.state.json")
    return data if isinstance(data, dict) else None


def read_statusline(sid):
    """Файл статусной строки <sid>.json — dict с добавленным "at" (mtime: сам файл времени не
    знает) или None."""
    if not sid:
        return None
    path = status_dir() / f"{safe_sid(sid)}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    data = _load_json(path)
    return {**data, "at": mtime} if isinstance(data, dict) else None


def entries_backwards(path, max_bytes=TAIL_MAX, needles=None):
    """Записи транскрипта (dict) от последней к первой, кусками по 1 МБ, не дальше max_bytes от
    конца. needles — строки байтов: разбирать только строки, где есть хоть одна (быстрый отсев)."""
    try:
        f = open(path, "rb")
    except (OSError, TypeError, ValueError):
        return
    with f:
        try:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
        except OSError:
            return
        stop = max(0, pos - max_bytes)
        tail = b""
        while pos > stop:
            step = min(1 << 20, pos - stop)
            pos -= step
            try:
                f.seek(pos)
                lines = (f.read(step) + tail).split(b"\n")
            except OSError:
                return
            tail = lines.pop(0) if pos > stop else b""  # первая строка куска может быть обрезана
            for raw in reversed(lines):
                if not raw or (needles and not any(n in raw for n in needles)):
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    yield entry


def _attachment_model(att):
    identity = att.get("identity")
    mid = identity.get("modelId") if isinstance(identity, dict) else None
    if isinstance(mid, str) and mid:
        return mid
    m = MODEL_IN_TEXT.search(str(att.get("text") or ""))
    return m.group(1) if m else None


def _scan_transcript(path):
    """Один проход с конца: {"tokens": (n|None, at, после_сжатия), "model": (id, at),
    "effort": (значение, at) | None}. Стоп — на последнем ответе основной ветки."""
    out = {}
    if not path:
        return out
    for e in entries_backwards(path, needles=(b'"assistant"', b'"model"', b"compact_boundary")):
        if e.get("isSidechain"):
            continue
        kind, at = e.get("type"), _iso_ts(e.get("timestamp"))
        if kind == "system" and e.get("subtype") == "compact_boundary":
            if "tokens" not in out:  # сразу после сжатия ответов ещё нет: размер — что сжатие оставило
                post = _count(_block(e, "compactMetadata").get("postTokens"))
                out["tokens"] = (post, at, True)
        elif kind == "attachment":  # вложение model — на старте, после сжатия и после /model
            att = _block(e, "attachment")
            if att.get("type") == "model" and "model" not in out:
                mid = _attachment_model(att)
                if mid:
                    out["model"] = (mid, at)
        elif kind == "assistant":
            msg = _block(e, "message")
            model = msg.get("model")
            if model == "<synthetic>":  # служебный ответ: модели нет, токены по нулям
                continue
            usage = _block(msg, "usage")
            tokens = sum(_count(usage.get(k)) or 0 for k in CONTEXT_KEYS)  # без output
            if "tokens" not in out and tokens:
                out["tokens"] = (tokens, at, False)
            if "model" not in out and isinstance(model, str) and model:
                out["model"] = (model, at)
            if "effort" not in out:
                out["effort"] = (e.get("effort"), at) if "effort" in e else None
        if "tokens" in out and "model" in out and "effort" in out:
            break
    return out


def _ultra_scan(path, sid):
    """Ultracode по транскрипту с кешем <sid>.ultra.json {path, offset, value}: дочитывается только
    прирост. Другой путь или файл короче offset — полный проход. Нет событий — False, ошибка — None."""
    try:
        if not os.path.isfile(path):
            return None
        size = os.path.getsize(path)
    except (OSError, TypeError, ValueError):
        return None
    cache_path = status_dir() / f"{safe_sid(sid)}.ultra.json" if sid else None
    cache = _load_json(cache_path) if cache_path else None
    offset, value = 0, None
    if isinstance(cache, dict) and cache.get("path") == str(path) and cache.get("value") in (True, False):
        off = _num(cache.get("offset"))
        if off is not None and 0 <= off <= size:
            offset, value = int(off), cache["value"]
    done = offset
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            buf = b""
            while True:
                chunk = f.read(ULTRA_CHUNK)
                if not chunk:
                    break
                buf += chunk
                cut = buf.rfind(b"\n") + 1  # только целые строки: недописанную прочтём в следующий раз
                if not cut:
                    continue
                if b"Ultracode" in buf[:cut] or b"ultra_effort_" in buf[:cut]:
                    last = None
                    for last in ULTRA_EVENT.finditer(buf, 0, cut):
                        pass
                    if last:
                        value = last.group(1) == b"on" or last.group(2) == b"enter"
                done += cut
                buf = buf[cut:]
    except OSError:
        return None
    if value is None:
        value = False
    if cache_path and (done != offset or not cache):
        write_json(cache_path, {"path": str(path), "offset": done, "value": value})
    return value


class _Sources:
    """Источники одной сессии: каждый читается не больше раза и только когда нужен."""

    def __init__(self, inp=None, sid=None, transcript=None, cwd=None, now=None):
        inp = inp if isinstance(inp, dict) else {}
        self.inp = inp
        self.sid = sid or inp.get("session_id") or None
        self.transcript = transcript or inp.get("transcript_path") or None
        self.cwd = cwd or inp.get("cwd") or None
        self.event = inp.get("hook_event_name")
        self.now = time.time() if now is None else now
        self._cache = {}

    def get(self, key, fn):
        if key not in self._cache:
            try:
                self._cache[key] = fn()
            except Exception:  # хук не должен падать ни на каком источнике
                self._cache[key] = None
        return self._cache[key]

    @property
    def mod(self):
        return self.get("mod", lambda: read_mod(self.sid))

    @property
    def statusline(self):
        return self.get("statusline", lambda: read_statusline(self.sid))

    @property
    def compact_at(self):
        return self.get("compact", lambda: read_compact(self.sid))

    @property
    def scan(self):
        return self.get("scan", lambda: _scan_transcript(self.transcript)) or {}


# --- группы полей ------------------------------------------------------------------------------


def _model(s):
    def pick():
        mod, st, tr = _block(s.mod, "model"), s.statusline or {}, s.scan.get("model")
        cands = []
        if isinstance(mod.get("id"), str) and mod["id"]:
            cands.append((_at(mod, s.mod), "mod", mod["id"]))
        if tr:
            cands.append((tr[1], "transcript", tr[0]))
        if isinstance(st.get("model"), str) and st["model"]:
            cands.append((st["at"], "statusline", st["model"]))
        best = _pick(cands)
        return (best[2], best[1]) if best else (None, None)
    return s.get("model", pick) or (None, None)


def _g_model(s):
    model, src = _model(s)
    st = s.statusline or {}
    name, name_src = model, src
    if model and isinstance(st.get("name"), str) and st["name"] and _same_model(st.get("model"), model):
        name, name_src = st["name"], "statusline"
    return {"model": model, "model_name": name, "src": {"model": src, "model_name": name_src}}


def _g_effort(s):
    hook = s.inp.get("effort")
    if isinstance(hook, dict):
        hook = hook.get("level")
    if hook not in (None, ""):
        return {"effort": hook, "src": {"effort": "hook"}}
    env = os.environ.get("CLAUDE_EFFORT")
    if env and s.event and s.event != "UserPromptSubmit":  # на промпте там значение со старта процесса
        return {"effort": env, "src": {"effort": "env"}}
    # null у источника — тоже показание: модель без effort (Haiku).
    cands = []
    mod = _block(s.mod, "effort")
    if "level" in mod:
        cands.append((_at(mod, s.mod), "mod", mod["level"]))
    st = s.statusline
    if st and "effort" in st:
        cands.append((st["at"], "statusline", st["effort"]))
    tr = s.scan.get("effort")
    if tr:
        cands.append((tr[1], "transcript", tr[0]))
    best = _pick(cands)
    return {"effort": best[2] if best else None, "src": {"effort": best[1] if best else None}}


def _g_ultracode(s):
    value = _ultra_scan(s.transcript, s.sid) if s.transcript else None
    if value is not None:
        return {"ultracode": value, "src": {"ultracode": "transcript"}}
    st = s.statusline or {}
    if isinstance(st.get("ultracode"), bool):
        return {"ultracode": st["ultracode"], "src": {"ultracode": "statusline"}}
    return {"ultracode": None, "src": {"ultracode": None}}


def _g_context(s):
    mod = s.mod or {}
    ctx = _block(mod, "context")
    at_mod = _at(ctx, mod)
    st = s.statusline or {}
    model = _model(s)[0]

    # Окно: мод или статусная строка (что свежее), иначе по модели.
    cands = []
    if _count(ctx.get("window")):
        cands.append((at_mod, "mod", _count(ctx["window"])))
    if _count(st.get("window")):
        cands.append((st["at"], "statusline", _count(st["window"])))
    best = _pick(cands)
    window, window_src = (best[2], best[1]) if best else _window_default(model)

    # Токены: мод или транскрипт (что свежее). Статусная строка — только запасной: её mtime
    # обновляется каждую секунду, а точность — 1 % окна.
    # Граница сжатия без postTokens — тоже показание: сжатие было, и замер мода до него устарел.
    cands = []
    if _count(ctx.get("tokens")) is not None:
        cands.append((at_mod, "mod", (_count(ctx["tokens"]), False)))
    tr = s.scan.get("tokens")
    if tr and (tr[0] is not None or tr[2]):
        cands.append((tr[1], "transcript", (tr[0], tr[2])))
    # Показания до прошедшего сжатия устарели (note_compact), и строка тогда — тоже по ним.
    cut = s.compact_at
    stale = cut is not None and not [c for c in cands if (c[0] or 0) >= cut]
    best = None if stale else _pick(cands)
    tokens, after_compact, tokens_src = None, stale, None
    if best:
        (tokens, after_compact), tokens_src = best[2], best[1]
    if tokens is None and not stale and _num(st.get("used_pct")) is not None and (_count(st.get("window")) or window):
        tokens = int(st["used_pct"] * (_count(st.get("window")) or window) / 100)
        tokens_src = "statusline~"

    auto = _count(ctx.get("auto_compact_at"))
    if auto:
        auto_src = "mod"
    else:
        auto, auto_src = _autocompact(model, s.cwd, window)
    enabled = ctx.get("auto_compact_enabled")
    if not isinstance(enabled, bool):
        enabled = True if _count(ctx.get("auto_compact_at")) else None
    if enabled is False:
        # Мод сказал «автосжатие выключено»: порог по формуле был бы выдумкой.
        auto, auto_src = None, "mod"
    return {"tokens": tokens, "after_compact": after_compact, "window": window,
            "auto_compact_at": auto, "auto_compact_enabled": enabled,
            "fill_pct": tokens * 100 // auto if tokens is not None and auto else None,
            "src": {"tokens": tokens_src, "window": window_src, "auto_compact_at": auto_src,
                    "auto_compact_enabled": "mod" if enabled is not None else None}}


def _context_fallback(s):
    model = _model(s)[0]
    window, src = _window_default(model)
    return {"tokens": None, "after_compact": False, "window": window,
            "auto_compact_at": max(1, window - AUTOCOMPACT_RESERVE), "auto_compact_enabled": None,
            "fill_pct": None, "src": {"tokens": None, "window": src, "auto_compact_at": "default",
                                      "auto_compact_enabled": None}}


def _group(s, fn, fallback):
    try:
        return fn(s)
    except Exception:
        return fallback(s)


def _empty(*names):
    return lambda s: {**{n: None for n in names}, "src": {n: None for n in names}}


# --- лимиты и дневная норма --------------------------------------------------------------------


def _limit_readings():
    """(src, окно, {used_percentage, resets_at}, at) из файлов мода за 8 суток и rate-limits.json."""
    d, wall = status_dir(), time.time()
    try:
        files = list(d.glob("*.state.json"))
    except OSError:
        files = []
    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if wall - mtime > LIMITS_MAX_AGE:
            continue
        data = _load_json(path)
        rl = _block(data, "rate_limits")
        for name in WINDOWS:
            w = _block(rl, name)
            if w:
                yield "mod", name, w, _num(w.get("at")) or _num(data.get("ts")) or mtime
    path = d / "rate-limits.json"
    data = _load_json(path)
    if isinstance(data, dict):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        for name in WINDOWS:
            w = _block(data, name)
            if w:
                yield "statusline", name, w, _num(data.get("ts")) or mtime


def read_limits(now=None):
    """Лимиты подписки {"five_hour": {...}, "seven_day": {...}} — в каждом used, resets_at, at,
    age (с), src ("mod" | "statusline"). Только окна со сбросом впереди: внутри окна процент только
    растёт, поэтому старое показание — нижняя граница и годно до сброса. Лимит общий для аккаунта:
    по окну берётся показание с поздним сбросом (с точностью до минуты), потом с большим процентом."""
    now = time.time() if now is None else now
    best = {}
    try:
        for src, name, w, at in _limit_readings():
            used, reset = _num(w.get("used_percentage")), _num(w.get("resets_at"))
            if used is None or reset is None:
                continue
            key = (round(reset / 60), used, at or 0)
            if name not in best or key > best[name][0]:
                best[name] = (key, {"used": used, "resets_at": reset, "at": at,
                                    "age": max(0.0, now - at) if at else None, "src": src})
    except Exception:
        return {}
    return {name: v for name, (_, v) in best.items() if v["resets_at"] > now}


def day_budget(d7, now=None):
    """Дневная норма недельного лимита — формула статусной строки («сегодня S/N%»): день
    начинается во время сброса недели; норма — остаток на начало дня ÷ оставшиеся рабочие дни
    (пн–пт) до сброса, выходной — один день. Начало дня — week-budget.json (общий со статусной
    строкой). Новый день модуль записывает сам (в Desktop статусной строки нет), но только по
    показанию, снятому уже в этих сутках: вчерашнее занизило бы начало дня.
    d7 — окно seven_day из read_limits (used, resets_at, at). Результат — {spent, norm, start,
    day_start} или None."""
    try:
        if not isinstance(d7, dict):
            return None
        used = _num(d7.get("used"))
        if used is None:
            used = _num(d7.get("used_percentage"))
        reset = _num(d7.get("resets_at"))
        if used is None or reset is None:
            return None
        now = time.time() if now is None else now
        if reset == int(reset):
            reset = int(reset)
        ws = reset - WEEK
        ds = ws + int((now - ws) // DAY) * DAY
        path = status_dir() / "week-budget.json"
        st = _load_json(path)
        st = st if isinstance(st, dict) else {}
        if st.get("day_start") == ds and _num(st.get("start_pct")) is not None:
            start = st["start_pct"]
        else:
            start = 0 if ds == ws else used
            at = _num(d7.get("at"))
            if at is not None and at >= ds:
                write_json(path, {"day_start": ds, "start_pct": start})
        weekday = lambda t: datetime.datetime.fromtimestamp(t).isoweekday()
        days = sum(1 for i in range(7) if ws + i * DAY >= ds and weekday(ws + i * DAY) <= 5)
        days += weekday(ds) > 5
        norm = (100 - start) / days if days else 100 - start
        return {"spent": max(0, used - start), "norm": norm, "start": start, "day_start": ds}
    except Exception:
        return None


# --- главное -----------------------------------------------------------------------------------


def session_state(inp=None, *, fields=FIELDS_ALL, sid=None, transcript=None, cwd=None, now=None):
    """Состояние сессии. inp — вход хука (session_id, transcript_path, cwd, effort,
    hook_event_name); sid/transcript/cwd — замена полей входа. Считаются только группы из fields:
      model     — model, model_name;
      effort    — effort;
      ultracode — ultracode;
      context   — tokens, after_compact, window, auto_compact_at, auto_compact_enabled, fill_pct;
      limits    — limits (read_limits);
      day       — day (day_budget по limits.seven_day).
    src — источник каждого поля групп model/effort/ultracode/context (mod, statusline,
    statusline~, transcript, hook, env, settings, default; None — данных нет); у лимитов src —
    внутри каждого окна. Поля невычисленных групп в результате отсутствуют."""
    if isinstance(fields, str):
        fields = (fields,)
    fields = set(fields or ())
    s = _Sources(inp, sid, transcript, cwd, now)
    out, src = {}, {}
    groups = (("model", _g_model, _empty("model", "model_name")),
              ("effort", _g_effort, _empty("effort")),
              ("ultracode", _g_ultracode, _empty("ultracode")),
              ("context", _g_context, _context_fallback))
    for name, fn, fallback in groups:
        if name in fields:
            part = _group(s, fn, fallback)
            src.update(part.pop("src"))
            out.update(part)
    if fields & {"limits", "day"}:
        limits = read_limits(s.now)
        if "limits" in fields:
            out["limits"] = limits
        if "day" in fields:
            out["day"] = day_budget(limits.get("seven_day"), s.now)
    out["src"] = src
    return out


def read_model(inp=None, *, sid=None, transcript=None, cwd=None, now=None):
    """model, model_name, effort и src."""
    return session_state(inp, fields=("model", "effort"), sid=sid, transcript=transcript, cwd=cwd,
                         now=now)


def read_context(inp=None, *, sid=None, transcript=None, cwd=None, now=None):
    """tokens, after_compact, window, auto_compact_at, auto_compact_enabled, fill_pct и src."""
    return session_state(inp, fields=("context",), sid=sid, transcript=transcript, cwd=cwd, now=now)


def read_ultracode(inp=None, *, sid=None, transcript=None, cwd=None, now=None):
    """ultracode и src."""
    return session_state(inp, fields=("ultracode",), sid=sid, transcript=transcript, cwd=cwd,
                         now=now)


def _find_transcript(sid):
    try:
        for d in (claude_dir() / "projects").iterdir():
            path = d / f"{sid}.jsonl"
            if path.is_file():
                return str(path)
    except OSError:
        pass
    return None


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    transcript = argv[1] if len(argv) > 1 else _find_transcript(argv[0])
    print(json.dumps(session_state(sid=argv[0], transcript=transcript), ensure_ascii=True, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
