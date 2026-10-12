"""Хук Claude Code: текущее время в контексте сессии и отметка в конце ответа.

Подкоманды (хук получает JSON события на stdin), подключение — hooks/hooks.json и scripts/link.py:
  prompt — UserPromptSubmit: «сейчас …, прошлый ответ … (N назад)» + правило отметки;
  tool   — PostToolUse: «сейчас …», не чаще раза в минуту на сессию;
  stop   — Stop: в конце ответа нет верной отметки «_ДД.ММ.ГГГГ ЧЧ:ММ <пояс>_» — вернуть сессию
           дописать её; ответ-прощание (слово из настройки) отметки не требует.

Часы — ключ "clock" в ~/.claude/claude-kit.json (его не трогает link.py --init):
  {"clock": {"utc_offset": 3, "label": "МСК", "farewell": "Пока."}}
utc_offset — часы от UTC (по умолчанию — пояс системы), label — подпись (по умолчанию «UTC+N»),
farewell — слово, которым кончается сессия (по умолчанию нет). Свои значения владелец держит в личном слое.

Где работает мод `mods/msk-time` (файл-признак `<claude>/msk-time/<sid>.mod` с `ok: true`), время в
конец ответа дописывает он, а не модель: правило говорит метку не писать, Stop её не требует. Модель досчитывала
время «на глаз» от последней заметки и уходила в будущее (04.10.2026) — проверка Stop ловила это уже
после того, как владелец увидел ответ.

Хук никогда не ломает сессию: любая ошибка — тихий выход с кодом 0. Сессии без человека
(`claude -p`, Agent SDK: CLAUDE_CODE_ENTRYPOINT=sdk-*) не трогает — их вывод читают скрипты.
Пояс — постоянное смещение, без перехода на летнее время; tzdata не нужна. Python 3.9+.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def load_clock() -> tuple[timezone, str, str | None]:
    """Пояс, его подпись и слово прощания из claude-kit.json; ошибка в конфиге — значения по умолчанию."""
    try:
        clock = json.loads((CLAUDE_DIR / "claude-kit.json").read_text("utf-8")).get("clock") or {}
    except Exception:
        clock = {}
    offset = clock.get("utc_offset")
    if isinstance(offset, (int, float)) and -14 <= offset <= 14:
        delta = timedelta(hours=offset)
    else:
        delta = datetime.now().astimezone().utcoffset() or timedelta(0)
    minutes = int(delta.total_seconds() // 60)
    default = f"UTC{'+' if minutes >= 0 else '-'}{abs(minutes) // 60}" + (f":{abs(minutes) % 60:02d}" if minutes % 60 else "")
    # "label": "" — время без подписи.
    label = clock.get("label") if isinstance(clock.get("label"), str) else default
    farewell = clock.get("farewell") if isinstance(clock.get("farewell"), str) and clock.get("farewell").strip() else None
    return timezone(delta), label.strip(), farewell.strip() if farewell else None


TZ, LABEL, FAREWELL = load_clock()
TAIL = f" {LABEL}" if LABEL else ""  # подпись после времени; пустая — ничего
STAMP_RE = re.compile(r"_(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2})" + re.escape(TAIL) + "_")
# Отметка верна, если отстаёт от момента остановки не больше чем на 4 мин и не опережает его.
LAG = timedelta(minutes=4)
AHEAD = timedelta(minutes=1)
# С прошлого ответа прошло столько — напомнить, что снаружи всё могло измениться.
GAP = timedelta(hours=2)
GAP_NOTE = ("Перерыв большой: состояние вне сессии (CI, MR, задачи, чужие правки) могло измениться — "
            "перепроверить, а не докладывать по памяти.")
STATE_DIR = CLAUDE_DIR / "msk-time"
KEEP = timedelta(days=60)

RULE = (
    f"Последняя строка итогового ответа владельцу — отметка курсивом «_ДД.ММ.ГГГГ ЧЧ:ММ{TAIL}_» "
    "по самому свежему «Сейчас …» из таких заметок (после инструментов они обновляются); "
    "время не выдумывать. Промежуточный текст между вызовами инструментов"
    + (f" и прощание «{FAREWELL}»" if FAREWELL else "") + " — без отметки."
)
RULE_MOD = (
    "Время в конец ответа дописывает программа (мод msk-time) — метку времени сам не пиши; "
    "заметки «Сейчас …» — для расчётов."
)


def fmt(dt: datetime) -> str:
    return f"{dt.astimezone(TZ):%d.%m.%Y %H:%M}{TAIL}"


def stamp(dt: datetime) -> str:
    return f"_{fmt(dt)}_"


def ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "меньше минуты"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} дн")
    if hours:
        parts.append(f"{hours} ч")
    if mins and not days:
        parts.append(f"{mins} мин")
    return " ".join(parts)


def mod_active(session: str) -> bool:
    """Мод msk-time в этой сессии ставит время сам (его файл-признак, `ok: true`)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", session)
    try:
        return json.loads((STATE_DIR / f"{name}.mod").read_text("utf-8")).get("ok") is True
    except Exception:
        return False


def load_state(session: str) -> dict:
    try:
        return json.loads((STATE_DIR / f"{session}.json").read_text("utf-8"))
    except Exception:
        return {}


def save_state(session: str, state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / f"{session}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), "utf-8")
    os.replace(tmp, path)


def prune_states(now: datetime) -> None:
    for path in [*STATE_DIR.glob("*.json"), *STATE_DIR.glob("*.mod")]:
        try:
            if now.timestamp() - path.stat().st_mtime > KEEP.total_seconds():
                path.unlink()
        except Exception:
            pass


def tail_entries(transcript: str, size: int = 512 * 1024):
    """Записи транскрипта (JSONL) с конца файла, от новых к старым."""
    try:
        f = open(transcript, "rb")
    except OSError:  # первый промпт новой сессии: транскрипта ещё нет
        return
    with f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - size))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            yield json.loads(line)
        except Exception:
            continue


def last_assistant_text(event: dict) -> str | None:
    text = event.get("last_assistant_message")
    if isinstance(text, str):
        return text
    transcript = event.get("transcript_path")
    if not transcript:
        return None
    for entry in tail_entries(transcript):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        texts = [c.get("text", "") for c in content or [] if c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return None


def last_activity(event: dict) -> datetime | None:
    """Время последней записи транскрипта — если своего состояния ещё нет."""
    transcript = event.get("transcript_path")
    if not transcript:
        return None
    for entry in tail_entries(transcript):
        ts = entry.get("timestamp")
        if entry.get("type") in ("assistant", "user") and ts:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return None


def emit(obj: dict) -> None:
    # ASCII-JSON: кодировка консоли Windows не испортит кириллицу.
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))


def context(event_name: str, text: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}})


def on_prompt(event: dict, now: datetime) -> None:
    session = event.get("session_id", "unknown")
    state = load_state(session)
    text = f"Сейчас {fmt(now)}."
    prev = state.get("last_stop") or state.get("last_prompt")
    prev_dt = datetime.fromisoformat(prev) if prev else last_activity(event)
    if prev_dt:
        what = "Прошлый ответ в этой сессии" if state.get("last_stop") else "Прошлая активность в этой сессии"
        text += f" {what} — {fmt(prev_dt)} ({ago(now - prev_dt)} назад)."
        if now - prev_dt >= GAP:
            text += f" {GAP_NOTE}"
    context("UserPromptSubmit", f"{text} {RULE_MOD if mod_active(session) else RULE}")
    state["last_prompt"] = now.isoformat()
    state["last_note"] = f"{now.astimezone(TZ):%Y-%m-%d %H:%M}"
    save_state(session, state)
    prune_states(now)


def on_tool(event: dict, now: datetime) -> None:
    if event.get("agent_id"):  # вызов внутри субагента — ему отметка не нужна
        return
    session = event.get("session_id", "unknown")
    state = load_state(session)
    minute = f"{now.astimezone(TZ):%Y-%m-%d %H:%M}"
    if state.get("last_note") == minute:
        return
    context("PostToolUse", f"Сейчас {fmt(now)}.")
    state["last_note"] = minute
    save_state(session, state)


def parse_stamp(text: str) -> datetime | None:
    found = STAMP_RE.findall(text[-400:])
    if not found:
        return None
    d, m, y, hh, mm = map(int, found[-1])
    try:
        return datetime(y, m, d, hh, mm, tzinfo=TZ)
    except ValueError:  # 31.02 и т. п.
        return None


def is_farewell(text: str) -> bool:
    """Ответ кончается словом прощания из настройки (так кончается сессия) — отметка не нужна."""
    if not FAREWELL:
        return False
    lines = [line.strip().strip("*_ ") for line in text.strip().splitlines()]
    return bool(lines) and lines[-1] == FAREWELL


def on_stop(event: dict, now: datetime) -> None:
    session = event.get("session_id", "unknown")
    state = load_state(session)
    if not event.get("stop_hook_active") and not mod_active(session):
        text = last_assistant_text(event)
        if text is not None:
            got = parse_stamp(text)
            # Отметка с точностью до минуты: сравниваем с началом текущей минуты.
            floor = now.replace(second=0, microsecond=0)
            ok = got is not None and floor - LAG <= got <= floor + AHEAD
            if not ok and not is_farewell(text):
                reason = (
                    f"В конце ответа нет верной отметки времени. Сейчас {fmt(now)}. "
                    f"Выведи одной строкой только это, без других слов: {stamp(now)}"
                )
                emit({"decision": "block", "reason": reason})
                return
    state["last_stop"] = now.isoformat()
    save_state(session, state)


HANDLERS = {"prompt": on_prompt, "tool": on_tool, "stop": on_stop}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        now = datetime.now(TZ)
        if (STATE_DIR / "debug").exists():
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATE_DIR / "debug.log", "a", encoding="utf-8") as log:
                brief = {k: (v[-150:] if isinstance(v, str) else v) for k, v in event.items()}
                log.write(f"{now.isoformat()} {mode} {json.dumps(brief, ensure_ascii=False)}\n")
        HANDLERS[mode](event, now)
    except Exception:
        if os.environ.get("MSK_TIME_TRACE"):
            raise


if __name__ == "__main__":
    main()
