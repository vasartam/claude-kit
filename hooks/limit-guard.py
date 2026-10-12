#!/usr/bin/env python3
"""limit-guard: сессия сама встаёт на паузу у лимита подписки и продолжает после сброса.

Без отдельной сессии-координатора. Лимиты 5 часов и 7 дней общие для аккаунта; хук берёт их у
session_state.read_limits (файлы мода session-state всех сессий и rate-limits.json статусной строки,
транскрипт не читается). Показание годно, пока его сброс впереди: внутри окна процент только растёт,
значит старое показание — нижняя граница. Хук читает лимиты в каждой сессии:

PreToolUse — перед каждым действием модели. Лимит достиг порога (по умолчанию 90%) — первое же
действие отклоняется с инструкцией поставить работу на паузу: дойти до безопасной точки, разложить
знания, записать план продолжения и запланировать себе продолжение на время сброса встроенным
заданием CronCreate. На это даётся GRACE минут, дальше любые действия отклоняются до сброса.

UserPromptSubmit — промпт во время паузы. Человек рядом и сам решает: хук напоминает модели про
лимит и время сброса и снова даёт GRACE минут на работу. Промпт продолжения от CronCreate
проходит как обычно.

После сброса проценты падают ниже порога, и хук забывает о паузе сам.

Сабагент (agent_id) паузу не начинает и инструкцию не получает: её ведёт основная сессия. Если пауза
уже идёт и GRACE прошло — отказ тот же.

Координатор жив — хук молчит: паузу и пробуждение тогда ведёт навык limit-guard (или координатор
night-work), а хук отказал бы самому координатору в инструментах и дал бы второе пробуждение.
Признак — mtime файлов ~/.claude/limit-guard/alive-limit-guard (свежее 30 мин) и alive-night-work
(свежее 75 мин); их пишут сами навыки на каждой своей проверке (раз в 20 мин и раз в час), содержимое
не важно. Реестр registry.md не учитывается: ночью лимиты ведёт координатор night-work, реестра не
трогает. Файлов нет (терминал, координатор не запущен) — хук работает сам.

Выключается файлом ~/.claude/limit-guard/off только до ближайшего сброса 5ч лимита: после сброса хук
удаляет файл сам и снова работает. Хук держать включённым: off — только совсем близко к лимиту, мешает —
чинить хук, а не выключать (README.md рядом).
Пороги — LIMIT_GUARD_5H, LIMIT_GUARD_7D (проценты); GRACE — LIMIT_GUARD_GRACE (минуты, 10).
Состояние — ~/.claude/limit-guard/<session_id>.json, план продолжения — там же, .resume.md.
Каталог ~/.claude — это CLAUDE_CONFIG_DIR, если задан.
"""
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from session_state import claude_dir, emit_json, read_input, read_limits, safe_sid, write_json
except ImportError:  # набор повреждён: хук молча пропускает работу, падать нельзя
    sys.exit(0)


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


WINDOWS = ("five_hour", "seven_day")
THRESHOLD = {"five_hour": _float_env("LIMIT_GUARD_5H", 90), "seven_day": _float_env("LIMIT_GUARD_7D", 90)}
NAMES = {"five_hour": "пятичасовой", "seven_day": "недельный"}
GRACE = _float_env("LIMIT_GUARD_GRACE", 10) * 60
RESUME_MARK = "Лимит сброшен (limit-guard)"
# Признаки живого координатора: файл (по mtime) → срок в секундах. Навык limit-guard проверяет раз в
# 20 минут, night-work — раз в час, отсюда разные сроки.
ALIVE = (("alive-limit-guard", 30 * 60), ("alive-night-work", 75 * 60))
SAME_RESET = 120  # сбросы ближе этого (с) — одно окно: источники округляют по-разному


def state_dir():
    return claude_dir() / "limit-guard"


def base(sid):
    return state_dir() / safe_sid(sid)


def load(sid):
    try:
        with open(f"{base(sid)}.json", "rb") as f:
            data = json.loads(f.read().decode("utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(sid, st):
    write_json(f"{base(sid)}.json", st)


def forget(path):
    try:
        os.remove(path)
    except OSError:
        pass


def over_limit(limits):
    """(ключ лимита, процент, время сброса) превышенного лимита или None. Если превышены оба,
    ждать надо до более позднего сброса."""
    hit = [(k, limits[k]["used"], limits[k]["resets_at"]) for k in WINDOWS
           if limits.get(k) and limits[k]["used"] >= THRESHOLD[k]]
    return max(hit, key=lambda h: h[2]) if hit else None


def when(ts):
    """«18:34», «завтра 09:00», «02.10 09:00»."""
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return f"{d:%H:%M}"
    return f"{'завтра' if d.date() == today + datetime.timedelta(1) else d.strftime('%d.%m')} {d:%H:%M}"


def cron_at(ts):
    d = datetime.datetime.fromtimestamp(ts + 180)  # через 3 минуты после сброса
    if d.minute in (0, 30):
        d += datetime.timedelta(minutes=1)
    return f"{d.minute} {d.hour} {d.day} {d.month} *"


def pause_instruction(sid, key, pct, reset):
    resume = f"{base(sid)}.resume.md"
    return (
        f"Лимит подписки (limit-guard): {NAMES[key]} лимит использован на {pct:.0f}%, сброс в {when(reset)}. "
        f"Поставь работу на паузу, не продолжая задачу: 1) если сейчас идёт правка, доведи её до целого "
        f"состояния, новых шагов не начинай; 2) вызови /session-extract-knowledge (источник — сессия, "
        f"концовка limit-guard: отчёт одной строкой, без сжатия); 3) если осталась незаконченная работа или "
        f"вопросы — запиши в {resume} задачу, её состояние, следующие шаги и что ждёт ответа пользователя, и "
        f"создай разовое задание CronCreate (recurring: false, cron \"{cron_at(reset)}\") с промптом "
        f"«{RESUME_MARK}: продолжай работу по плану из {resume}»; если работы не осталось — шаг 3 пропусти; "
        f"4) одной строкой скажи пользователю, что сессия на паузе до {when(reset)}, и закончи ход. "
        f"На всё это есть {GRACE / 60:.0f} мин, потом действия будут отклоняться до сброса.")


def same_pause(st, reset):
    r = st.get("reset")
    return isinstance(r, (int, float)) and abs(r - reset) < SAME_RESET


def on_tool(inp, sid, limits, now):
    hit = over_limit(limits)
    st = load(sid)
    if not hit:
        if st:
            forget(f"{base(sid)}.json")
        return
    key, pct, reset = hit
    if not same_pause(st, reset):  # новая пауза: первое действие отклоняем с инструкцией
        if inp.get("agent_id"):
            return  # сабагент паузу не начинает: инструкцию получит основная сессия
        save(sid, {"reset": reset, "started": now})
        reason = pause_instruction(sid, key, pct, reset)
    elif now - (st.get("started") or 0) < GRACE:
        return  # время на то, чтобы сохранить знания и запланировать продолжение
    else:
        reason = (f"Лимит подписки (limit-guard): сессия на паузе до {when(reset)} ({NAMES[key]} лимит "
                  f"{pct:.0f}%). Действия отклоняются; закончи ход одной строкой.")
    emit_json({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": reason}})


def on_prompt(inp, sid, limits, now):
    hit = over_limit(limits)
    if inp.get("agent_id") or not hit or str(inp.get("prompt") or "").lstrip().startswith(RESUME_MARK):
        return
    key, pct, reset = hit
    fresh = not same_pause(load(sid), reset)
    save(sid, {"reset": reset, "started": now})  # человек рядом: снова даём время на работу
    if fresh:
        ctx = pause_instruction(sid, key, pct, reset) + (
            " Если пользователь в этом промпте явно просит работать несмотря на лимит — выполни просьбу вместо паузы.")
    else:
        ctx = (f"Лимит подписки (limit-guard): {NAMES[key]} лимит {pct:.0f}%, сессия на паузе до {when(reset)}. "
               f"Если пользователь явно просит работать несмотря на лимит — работай; иначе одной строкой "
               f"напомни про паузу и время возобновления.")
    emit_json({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}})


def coordinator_alive(now):
    for name, ttl in ALIVE:
        try:
            if now - (state_dir() / name).stat().st_mtime < ttl:
                return True
        except OSError:
            pass
    return False


def off_active(limits):
    """Выключатель off действует до ближайшего сброса 5ч лимита: файл, созданный до начала текущего
    пятичасового окна, удаляется, и хук снова работает."""
    off = state_dir() / "off"
    try:
        mtime = off.stat().st_mtime
    except OSError:
        return False
    h5 = limits.get("five_hour")
    if h5 and mtime < h5["resets_at"] - 5 * 3600:
        forget(off)
        return False
    return True


HANDLERS = {"PreToolUse": on_tool, "UserPromptSubmit": on_prompt}


def main():
    inp = read_input("LIMIT_GUARD_DUMP")
    if not inp:
        return
    sid, handler = inp.get("session_id"), HANDLERS.get(inp.get("hook_event_name"))
    if not isinstance(sid, str) or not sid or not handler:
        return
    now = time.time()
    if coordinator_alive(now):
        return
    limits = read_limits(now)
    if off_active(limits):
        return
    handler(inp, sid, limits, now)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # без данных или на странном вводе хук молча пропускает работу
        pass
