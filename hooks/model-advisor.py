#!/usr/bin/env python3
"""UserPromptSubmit: строка «model-advisor: …» — текущие модель, effort, контекст и лимиты; совет
сменить настройки и ожидание ответа решает код, а не модель.

Данные хук берёт у session_state.py: модель, effort, ultracode, заполнение контекста относительно
автосжатия, лимиты подписки 5ч/7д и дневная норма недельного — каждое из самого свежего источника
(мод session-state, статусная строка, транскрипт). Нет ни модели, ни лимитов — хук молчит.

Совет (decide) — формальная таблица, без оценки задачи:
  повод (tight): 5ч ≥ TIGHT_5H % или за день потрачена дневная норма недельного;
  что сменить (cheaper): ultracode вкл → выкл; Fable → Opus; иначе effort xhigh/max → high;
  иначе Opus → Sonnet. Sonnet/Haiku с effort до high — менять нечего, совета нет.
  Раз на ситуацию: ситуация — окно повода (сброс 5ч, начало дня нормы); выданные ключи — в
  session-status/<sid>.advice.json. Совет проигнорирован или принят — до нового окна молчит.
Есть совет — в строке «Совет: …»; под herdr (HERDR_PANE_ID) ещё `wait: ~/.claude/bin/claude-autocontinue
<sid> 120`: тот ждёт ответа 2 мин (MODEL_ADVISOR_WAIT) и, если ответа нет, сам пишет агенту «продолжай»;
следующий промпт (ответ на совет) хук пропускает по файлу session-status/<sid>.advised. Совета нет —
`wait:` нет никогда. Оценка «задача тяжелее/проще» — за моделью, без ожидания (knowledge/model-advice.md).
Под herdr ещё метка вкладки — и условие: менять только при смене основной задачи.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from session_state import (FIELDS_ALL, _load_json, emit_context, read_input, safe_sid, session_state,
                               status_dir, write_json)
except ImportError:  # набор повреждён: хук молча пропускает работу, падать нельзя
    sys.exit(0)


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


WAIT = _int_env("MODEL_ADVISOR_WAIT", 120)
WAIT_TEXT = f"{WAIT // 60} мин" if WAIT % 60 == 0 else f"{WAIT} с"
TIGHT_5H = 70  # % пятичасового лимита, с которого настройки дороже нужного — повод удешевить
STALE_AGE = 15 * 60  # данные лимитов старше — в строке помечаются
# Раскладка знаний (навык с префиксом плагина или без) и служебные промпты claude-compact / claude-autocontinue.
SKIP = re.compile(r"\s*(/(?:[\w-]+:)?(session-extract-knowledge|session-end)\b|Контекст сжат[,( ]"
                  r"|Продолжай: на совет по модели)")
RULES = "Правила — «Модель и effort» корневых правил и knowledge/model-advice.md."
COSTLY_EFFORT = ("xhigh", "max")


def fmt_time(ts, pattern):
    try:
        return datetime.datetime.fromtimestamp(ts).strftime(pattern)
    except (ValueError, OverflowError, OSError, TypeError):
        return "?"


def limits_text(st):
    """(текст лимитов, поводы, возраст самого старого показания в секундах).

    Повод — ключ ситуации «лимит на исходе»: «5h:<сброс в минутах>» (5ч ≥ TIGHT_5H %) и
    «day:<начало дня>» (за день потрачена дневная норма). Ключ живёт до конца окна: по нему хук
    даёт совет один раз. Дневная норма — та же формула, что «сегодня X/Y%» в статусной строке:
    остаток на начало дня ÷ оставшиеся рабочие дни (пн–пт) до сброса. Прежний «темп к сбросу»
    вечером завышал прогноз: 01.10.2026 он дважды пугал «~91%» при 16% из нормы 22%."""
    lim, day = st.get("limits") or {}, st.get("day")
    parts, triggers, ages = [], [], []
    h5, d7 = lim.get("five_hour"), lim.get("seven_day")
    if h5:
        parts.append(f"5ч {h5['used']:.0f}% (сброс {fmt_time(h5['resets_at'], '%H:%M')})")
        if h5["used"] >= TIGHT_5H:
            triggers.append(f"5h:{round(h5['resets_at'] / 60)}")
        ages.append(h5.get("age"))
    if d7:
        today = f", сегодня {day['spent']:.0f}/{day['norm']:.0f}% нормы" if day else ""
        parts.append(f"7д {d7['used']:.0f}% (сброс {fmt_time(d7['resets_at'], '%d.%m %H:%M')}{today})")
        if day and day["spent"] >= day["norm"]:
            triggers.append(f"day:{day.get('day_start')}")
        ages.append(d7.get("age"))
    return ", ".join(parts), triggers, max((a for a in ages if a is not None), default=0)


def family(model):
    """Семейство модели по id или имени: fable, opus, sonnet, haiku или None."""
    low = str(model or "").lower()
    return next((f for f in ("fable", "opus", "sonnet", "haiku") if f in low), None)


def cheaper(st):
    """Что сменить на ступень дешевле — список шагов; пусто — настройки уже дешёвые или неизвестны."""
    steps = []
    if st.get("ultracode") is True:
        steps.append("ultracode выкл")
    fam, effort = family(st.get("model")), st.get("effort")
    if fam == "fable":
        steps.append("Fable → Opus")
    elif effort in COSTLY_EFFORT:
        steps.append(f"effort {effort} → high")
    elif fam == "opus":
        steps.append("Opus → Sonnet")
    return steps


def decide(st, given):
    """(совет или None, новые ключи ситуаций). Совет — когда лимит на исходе, есть что удешевить и
    хотя бы одна из ситуаций ещё без совета (given — уже выданные ключи)."""
    _, triggers, _ = limits_text(st)
    fresh = [t for t in triggers if t not in given]
    steps = cheaper(st) if fresh else []
    if not steps:
        return None, []
    return ", ".join(steps), fresh


def tab_label():
    """Метка вкладки herdr без номера «[N] », который дописывает плагин; без вкладки или при сбое — ""."""
    tab = os.environ.get("HERDR_TAB_ID")
    if not tab:
        return ""
    try:
        herdr = shutil.which("herdr") or str(Path.home() / ".local" / "bin" / "herdr")
        out = subprocess.run([herdr, "tab", "get", tab], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=2).stdout
        label = json.loads(out)["result"]["tab"]["label"] or ""
    except Exception:  # herdr нет, завис или ответил не так — метки просто не будет
        return ""
    return re.sub(r"^\[\d+\]\s*", "", str(label))


def advice_line(st, sid, advice=None):
    name = st.get("model_name") or st.get("model") or "неизвестно"
    effort = st.get("effort")
    text = f"model-advisor: {name}, effort {'?' if effort in (None, '') else effort}"
    if st.get("tokens") is not None and st.get("auto_compact_enabled") is False:
        # Мод сказал «автосжатие выключено»: порог по формуле был бы выдумкой.
        text += f", контекст {st['tokens'] // 1000}k, автосжатие выкл"
    elif st.get("tokens") is not None and st.get("fill_pct") is not None:
        text += (f", контекст {st['tokens'] // 1000}k — {st['fill_pct']}% до автосжатия "
                 f"({st['auto_compact_at'] // 1000}k)")
    text += ", ultracode " + {True: "вкл", False: "выкл"}.get(st.get("ultracode"), "?")
    lim, triggers, age = limits_text(st)
    if lim:
        text += f"; {lim}"
        if age > STALE_AGE:
            text += f", лимиты — данные {round(age / 60)} мин назад"
    if triggers:
        text += "; limits tight"
    text += ". " + RULES
    herdr = bool(os.environ.get("HERDR_PANE_ID"))
    if advice:
        text += f" Совет (лимиты на исходе): {advice} — предложить до работы."
        if herdr:
            text += f" wait: `~/.claude/bin/claude-autocontinue {sid} {WAIT}` ({WAIT_TEXT})."
    if herdr:
        label = tab_label()
        if label:
            # Без метки перед глазами модель забывала правило (01.10.2026 вкладка так и называлась по
            # первой задаче). Но приказ «задача сменилась — переименовать» к каждому промпту толкал
            # переименовывать по побочной просьбе (13.10.2026) — поэтому только условие.
            text += (f" Вкладка herdr: «{label}» — менять только при смене основной задачи "
                     "(правила слоя terminal).")
    return text


def read_given(path):
    data = _load_json(path)
    keys = data.get("given") if isinstance(data, dict) else None
    return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []


def main():
    inp = read_input()
    if not inp or inp.get("agent_id"):
        return
    sid = inp.get("session_id")
    if not isinstance(sid, str) or not sid or SKIP.match(str(inp.get("prompt") or "")):
        return
    advised = status_dir() / f"{safe_sid(sid)}.advised"
    if advised.exists():
        try:
            advised.unlink()  # это ответ на совет: «продолжай» или уже переключил
        except OSError:
            pass
        return
    st = session_state(inp, fields=FIELDS_ALL)
    if not st.get("model") and not st.get("limits"):
        return
    state = status_dir() / f"{safe_sid(sid)}.advice.json"
    given = read_given(state)
    advice, fresh = decide(st, given)
    if fresh:
        # Записать до вывода: не записалось — совет не давать, иначе он повторится на каждом промпте.
        if not write_json(state, {"given": (given + fresh)[-20:]}):
            advice = None
    emit_context("UserPromptSubmit", advice_line(st, sid, advice))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # без данных или на странном вводе хук молча пропускает работу
        pass
