#!/usr/bin/env python3
"""context-guard: знания раскладываются по местам до того, как сжатие контекста их сотрёт.

Один хук на обе оболочки (терминал в herdr и без него, приложение), одна точка входа на все события
(событие — из hook_event_name во входе), одно состояние: ~/.claude/context-guard/<session_id>.json.
Размер контекста, окно и порог автосжатия — из session_state.py рядом (мод session-state, статусная
строка, транскрипт — что свежее). Схема одна; от оболочки зависит только концовка — есть ли панель
herdr ($HERDR_PANE_ID): в ней сжатие и раскладку ведёт агенту herdr; без неё — мод context-compact
(модель после раскладки выполняет `echo context-compact [--resend <файл>] -- <инструкция>`, мод после хода
сжимает и присылает запрос заново), а без мода сжимает владелец.

UserPromptSubmit — проверка на старте промпта. Модель уже видит следующую задачу и решает, нужен ли
для неё весь текущий контекст. С BASE_eff (300k) к промпту добавляется вопрос:
  - не нужен — раскладка (/session-extract-knowledge) и сжатие. В панели herdr модель к запросу не
    приступает, а в конце раскладки зовёт `~/.claude/bin/claude-compact --resend <файл с промптом>`:
    после сжатия запрос приходит снова. Без панели модель раскладывает знания, выполняет запрос и
    последней строкой просит владельца набрать `/compact <инструкция>`;
  - нужен — модель выполняет запрос и пишет в конце ответа «> контекст Nk, проверка отложена до Mk»;
    хук берёт порог из этой строки на следующем промпте (не дальше STEP от текущего). Вызывать `defer`
    не нужно: в режиме auto классификатор блокирует его как самомодификацию.
    Вручную: context-guard.py defer <session_id> <токены>   (650000, 650k; «650» без единиц = 650k)
С HARD_eff отложить нельзя. Ниже BASE_eff (после сжатия) отложенный порог сбрасывается. Порог
следующей проверки — в session-status/<session_id>.ctx-mark: статусная строка показывает его «⚑650k».
HARD_eff = min(HARD, порог автосжатия; выключено — 92 % окна), BASE_eff = min(BASE, max(HARD_eff − STEP,
60 % HARD_eff)).

PostToolUse — отметка `echo knowledge-sorted` (Bash): знания разложены на нынешнем размере контекста.
Переходное от knowledge_guard: `echo knowledge-keep-context 700k <зачем>` — отсрочка проверки до 700k
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
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from session_state import (block_stderr, claude_dir, cleanup_status, emit_context, emit_json,
                               entries_backwards, note_compact, parse_tokens, read_input, safe_sid, session_state,
                               status_dir, write_json)
except ImportError:  # набор повреждён: хук молча пропускает работу, падать нельзя
    sys.exit(0)


def _env(*names):
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _tokens_env(default, *names):
    """Токены из переменной: «300000» — как есть, «300k», «0.3m» — по parse_tokens; мусор — default."""
    raw = _env(*names)
    if raw is None:
        return default
    n = int(raw) if raw.isdigit() else parse_tokens(raw)
    return default if n is None else n


def _float_env(default, *names):
    raw = _env(*names)
    try:
        return default if raw is None else float(raw)
    except ValueError:
        return default


BASE = _tokens_env(300_000, "CONTEXT_GUARD_BASE")
HARD = _tokens_env(900_000, "CONTEXT_GUARD_HARD")
# Отсрочка — не дальше этого шага от текущего контекста: модели охотно откладывают на «+150k» по инерции.
STEP = _tokens_env(150_000, "CONTEXT_GUARD_STEP")
COMPACT_SLACK = 10  # с: сжатие без отметки PreCompact считать начатым настолько раньше SessionStart
AUTO_CEILING = _float_env(0.92, "CONTEXT_GUARD_AUTO_CEILING")
MIN_UNSORTED = _tokens_env(20_000, "CONTEXT_GUARD_MIN_UNSORTED", "KNOWLEDGE_GUARD_MIN_UNSORTED")
# Предел переходной `echo knowledge-keep-context` сверх HARD_eff; не задан — только HARD_eff.
KEEP_MAX = _tokens_env(None, "CONTEXT_GUARD_KEEP_MAX", "KNOWLEDGE_GUARD_KEEP_MAX")
REWAKE_DELAY = _float_env(2.0, "CONTEXT_GUARD_REWAKE_DELAY", "KNOWLEDGE_GUARD_REWAKE_DELAY")
AUTO_RESERVE = 40_000  # без панели: столько токенов до края окна оставить самому сжатию
# Отказов автосжатию подряд, дальше оно проходит: с autoCompactWindow 500k при окне 1M отказы иначе
# шли бы до потолка 920k.
AUTO_MAX_BLOCKS = 40
PENDING_TTL = 30 * 60  # раскладка агенту в панель herdr — не чаще
PENDING_REPEAT = 4  # пока сжатие отложено, напоминание — на первом и каждом 4-м вызове инструмента
STATE_TTL_DAYS = 30

SKILL = "/session-extract-knowledge"
SKILL_TEXT = "навык session-extract-knowledge"
MARKER = "knowledge-sorted"
KEEP = "knowledge-keep-context"
QUOTES = "весь сигнал — в двойных кавычках, внутри без `\"`, `$` и обратных кавычек: иначе `;` и `&&` в тексте режут команду"
DONE = f"В конце выполни отдельной командой Bash `echo {MARKER}` — это отметка для хука."
EXTRACT_SKILLS = ("session-extract-knowledge", "session-end")
# Навык раскладки — с префиксом плагина или без: «/session-end», «/<плагин>:session-end» (переходный срок,
# пока на машине жив прежний плагин с этими навыками).
EXTRACT_PROMPT = re.compile(r"\s*/(?:[\w-]+:)?(?:%s)\b" % "|".join(EXTRACT_SKILLS))
EXTRACT_COMMAND = re.compile(r"<command-name>/(?:[\w-]+:)?(?:%s)</command-name>" % "|".join(EXTRACT_SKILLS))
MARKER_CMD = re.compile(r"(?:^\s*|[;&|(]\s*)echo\s+[\"']?" + MARKER + r"\b")
KEEP_CMD = re.compile(r"(?:^\s*|[;&|(]\s*)echo\s+[\"']?" + KEEP + r"\s+([\d.]+\s*[kKmM]?)(.*)")
# Только строка-цитата в начале строки: пример пометки внутри текста ответа (в `…`, в списке) — не отсрочка.
DEFER_NOTE = re.compile(r"^>\s*контекст[^\n]*?проверка отложена до\s*(\d+(?:[.,]\d+)?\s*[kKкКmMмМ]?)", re.M)


# --- состояние ---------------------------------------------------------------------------------


def state_path(sid):
    return claude_dir() / "context-guard" / f"{safe_sid(sid)}.json"


def prompt_path(sid):
    return claude_dir() / "context-guard" / f"{safe_sid(sid)}.prompt.md"


def fresh_state():
    return {"baseline": None, "sorted_at": None, "tokens": None, "next": None, "pending": None,
            "pending_at": 0, "pending_hits": 0, "rewoken_at": 0, "custom_instructions": None,
            "manual_blocked": False, "auto_blocks": 0, "compacted_unsorted": None,
            "extract_pending_at": None, "compact_started_at": None}


def compact_mod(sid):
    """Мод context-compact в этой сессии сжимает сам (его файл-признак, `ok: true`)."""
    data = _read(claude_dir() / "context-guard" / f"{safe_sid(sid)}.compact-mod")
    return bool(data) and data.get("ok") is True


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_state(sid):
    """Состояние сессии или None. Своего нет — берётся состояние прежнего knowledge_guard: переход без
    волны напоминаний. Прежний файл context-guard без baseline получает его на первом замере."""
    data = _read(state_path(sid))
    if data is None:
        data = _read(claude_dir() / "knowledge-guard" / f"{safe_sid(sid)}.json")
        if data is not None and data.get("keep_until") and not data.get("next"):
            data["next"] = data["keep_until"]  # отсрочка автосжатия knowledge_guard — теперь отсрочка проверки
    if data is None:
        return None
    st = {**fresh_state(), **data}
    if st["pending"] not in ("manual", "auto"):
        st["pending"] = None  # «review» knowledge_guard: отсрочки теперь — порог next
    return st


def load(sid):
    st = read_state(sid)
    if st is None:
        # Хук поставлен посреди идущей сессии: всё, что в ней было, считаем неразложенным.
        st = fresh_state()
        st["baseline"] = 0
    return st


def save(sid, st):
    write_json(state_path(sid), st)


def cleanup():
    cutoff = time.time() - STATE_TTL_DAYS * 86400
    for name in ("context-guard", "knowledge-guard"):
        try:
            files = list((claude_dir() / name).iterdir())
        except OSError:
            continue
        for f in files:
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
    cleanup_status()


# --- замер и пороги ----------------------------------------------------------------------------


def measure(inp=None, sid=None):
    """Замер контекста (session_state) и пороги этой сессии: hard — HARD_eff, base — BASE_eff."""
    s = session_state(inp, fields=("model", "context"), sid=sid)
    auto = s.get("auto_compact_at")  # None — автосжатие выключено
    window = s.get("window") or 1_000_000
    # Отсрочка дальше порога автосжатия бессмысленна: там всё равно сработает PreCompact. Автосжатие
    # выключено — предел у потолка окна: у 200k-модели HARD (900k) не наступил бы никогда.
    hard = min(HARD, auto or int(AUTO_CEILING * window))
    # При пороге автосжатия ниже BASE (200k-модели, autoCompactWindow < 333k) вопрос иначе не задавался бы
    # никогда: окно 1M с autoCompactWindow 500k — 467k/300k, 200k-модель — 167k/100k.
    base = min(BASE, max(hard - STEP, hard * 6 // 10))
    return {"tokens": s.get("tokens"), "window": window, "after_compact": bool(s.get("after_compact")),
            "hard": hard, "base": base}


def unsorted(st, cur):
    return max(0, cur - max(st["baseline"] or 0, st["sorted_at"] or 0))


def note_baseline(st, m):
    """Первый замер цикла — база: что было до него (после сжатия — сводка), разложено."""
    if st["baseline"] is None and not m["after_compact"]:
        st["baseline"] = m["tokens"]


def write_mark(sid, st, m):
    """Отметка для статусной строки: на каком размере контекста следующая проверка. Файл необязательный:
    claude-statusline показывает «⚑650k», только если он есть, и о context-guard больше ничего не знает."""
    # Порог, на котором спросит хук; если он уже пройден (проверка идёт сейчас) — тот же порог, а не предел.
    mark = st.get("next") or m["base"]
    write_json(status_dir() / f"{safe_sid(sid)}.ctx-mark", {"at": int(min(mark, m["hard"]))})


def set_next(st, n, hard, step=True):
    """Отложить проверку до n токенов: круглым числом (до 10k вверх: «410k», а не «401829»), не дальше
    hard, с step — и не дальше STEP от текущего. None — n не больше текущего контекста."""
    cur = st.get("tokens") or 0
    if n <= cur:
        return None
    top = min(n, cur + STEP) if step else n
    st["next"] = min(hard, -(-top // 10_000) * 10_000)
    return st["next"]


def k(n):
    return f"{int(n) // 1000}k"


# --- транскрипт --------------------------------------------------------------------------------


def is_boundary(e):
    return e.get("type") == "system" and e.get("subtype") == "compact_boundary"


def is_extraction(e):
    content = (e.get("message") or {}).get("content")
    if e.get("type") == "assistant" and isinstance(content, list):
        return any(b.get("type") == "tool_use" and b.get("name") == "Skill"
                   and str((b.get("input") or {}).get("skill", "")).split(":")[-1] in EXTRACT_SKILLS
                   for b in content if isinstance(b, dict))
    if e.get("type") == "user" and isinstance(content, str):
        return bool(EXTRACT_COMMAND.search(content))
    return False


def is_user_prompt(e):
    if e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain") or e.get("isCompactSummary"):
        return False
    content = (e.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return isinstance(content, str) and bool(content.strip()) and not content.lstrip().startswith("<")


def is_extraction_running(path):
    """Раскладка вызвана после последнего сжатия — идёт или закончилась."""
    for e in entries_backwards(path):
        if is_boundary(e):
            return False
        if is_extraction(e):
            return True
    return False


def extracted_since_compact(path):
    """Нечего раскладывать: после последней раскладки промптов пользователя не было, а если раскладки
    после прошлого сжатия не было — не было промптов и после самого сжатия."""
    for e in entries_backwards(path):
        if is_boundary(e) or is_extraction(e):
            return True
        if is_user_prompt(e):
            return False  # промпт новее раскладки или сжатия: дальше назад читать незачем
    return False


def deferred_in_last_answer(path):
    """Порог из пометки «> контекст Nk, проверка отложена до Mk» в ответе на прошлый промпт."""
    for e in entries_backwards(path):
        if is_user_prompt(e) or is_boundary(e):
            return None
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text":
                m = DEFER_NOTE.search(b.get("text", ""))
                if m:
                    return parse_tokens(m[1])
    return None


# --- тексты ------------------------------------------------------------------------------------


def ending(sid, prompt):
    """Концовка вопроса — как сжать контекст: (что сделать, что будет с запросом после сжатия). В панели
    herdr сжимает claude-compact, без неё — мод context-compact; оба присылают запрос заново. Без панели
    и мода сжать может только владелец."""
    panel, mod = bool(os.environ.get("HERDR_PANE_ID")), compact_mod(sid)
    if panel or mod:
        path = prompt_path(sid)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prompt, encoding="utf-8")
        except OSError:
            pass
        call = (f"вызови `~/.claude/bin/claude-compact --resend {path}`" if panel else
                f"выполни отдельной командой Bash `echo \"context-compact --resend {path} -- <инструкция>\"` "
                f"(сожмёт мод после хода; {QUOTES})")
        return (f"к запросу не приступай: вызови скилл {SKILL} (отчёт — одной строкой) и в конце {call}, а в "
                f"инструкции для /compact укажи, какая работа шла и что осталось незакрытым. После сжатия этот "
                f"запрос придёт снова, и ты выполнишь его уже в сжатом контексте", ", а запрос придёт снова")
    return (f"вызови скилл {SKILL} (отчёт — одной строкой), затем выполни запрос, а последней строкой ответа, "
            f"попроси владельца набрать `/compact <инструкция>` — инструкция "
            f"одной строкой до 250 символов: какая работа шла и что осталось незакрытым", "")


def question(sid, prompt, cur, m):
    extract, after = ending(sid, prompt)
    hard = m["hard"]
    if cur >= hard:
        return (f"Проверка контекста (context-guard): в контексте {k(cur)} токенов — это предел {k(hard)}, "
                f"откладывать нельзя. Прежде чем выполнять этот запрос, {extract}.")
    return (f"Проверка контекста (context-guard): в контексте {k(cur)} токенов. По умолчанию — сжать: {extract}. "
            f"Большая часть такого контекста — вывод команд, прочитанные файлы и обсуждение прошлых задач; после "
            f"сжатия остаются сводка с текущей задачей и её состоянием, пути к разложенным файлам{after}. Не "
            f"сжимать можно только по конкретной причине: этот запрос — продолжение текущего шага, и ему "
            f"нужны подробности, которые не переживут сводку и которых нет в файлах (точный вывод, несохранённое "
            f"состояние, середина правки). «Задача ещё идёт» сама по себе не причина. Если причина есть — выполняй "
            f"запрос и последней строкой ответа (после метки времени, если пишешь её) допиши строку-цитату «> контекст "
            f"{k(cur)}, проверка отложена до <порог>k: <причина в 3–6 словах>», порог — не дальше "
            f"{k(min(hard, cur + STEP))} (хук дальше не пустит); бери ровно столько, сколько нужно на этот шаг, "
            f"например «до {k(min(hard, cur + 50_000))}». Пометка — только в ответе на этот промпт, в следующих "
            f"её не повторяй: хук спросит снова сам. На {k(hard)} отложить будет нельзя.")


def compact_waits(cur, sid):
    """Без панели знания уже разложены, а сжатия ещё не было: раскладку не повторять."""
    how = (f"последней командой хода выполни отдельной командой Bash `echo \"context-compact -- <инструкция>\"` "
           f"(сожмёт мод после хода; {QUOTES})" if compact_mod(sid) else
           "последней строкой ответа напомни владельцу набрать `/compact <инструкция>`")
    return (f"Проверка контекста (context-guard): в контексте {k(cur)} токенов, знания уже разложены — сжатия "
            f"ещё не было. Раскладку не повторяй: выполни запрос и {how} — инструкция одной строкой до "
            f"250 символов: какая работа шла и что осталось незакрытым.")


def pending_text(st, cur, window, sid=None):
    """Напоминание, пока сжатие отложено (без панели): ручное — до повторного /compact, авто — до отметки."""
    if st["pending"] == "manual":
        again = "/compact"
        if st.get("custom_instructions"):
            again += " " + str(st["custom_instructions"])
        if sid and compact_mod(sid):
            arg = f" -- {st['custom_instructions']}" if st.get("custom_instructions") else " -- <инструкция>"
            return (f"context-guard: владелец вызвал /compact, хук отложил сжатие — сначала разложи "
                    f"знания этой сессии по полочкам ({SKILL_TEXT}, полный режим). {DONE} Потом ответь "
                    f"владельцу одной строкой, что куда записано, и последней командой хода выполни "
                    f"отдельной командой Bash `echo \"context-compact{arg}\"` ({QUOTES}) — мод повторит /compact сам.")
        return (f"context-guard: владелец вызвал /compact, хук отложил сжатие — сначала разложи "
                f"знания этой сессии по полочкам ({SKILL_TEXT}, полный режим). {DONE} Потом ответь "
                f"владельцу одной строкой: что куда записано и «можно {again}» (повторный "
                "/compact пройдёт в любом случае).")
    return (f"context-guard: автосжатие контекста отложено — знания с последнего выноса не "
            f"записаны (~{k(unsorted(st, cur))}). Запас до края окна ~{k(max(0, window - cur))}, сжатию "
            f"нужно ~{k(AUTO_RESERVE)}. Прямо сейчас, коротко: только незаписанное — решения, замеры, "
            f"баги, грабли, открытые хвосты текущей задачи ({SKILL_TEXT}, короткий режим); большие файлы "
            f"целиком не читать. {DONE} Потом продолжай прерванную работу.")


# --- события -----------------------------------------------------------------------------------


def on_prompt(inp, st, sid):
    prompt = inp.get("prompt")
    prompt = prompt if isinstance(prompt, str) else ""
    if EXTRACT_PROMPT.match(prompt):
        return
    m = measure(inp)
    cur = m["tokens"]
    if cur is None:
        return
    note_baseline(st, m)
    st["tokens"] = cur
    text = None
    if st["pending"]:
        # Сжатие отложено и ждёт раскладки (без панели): напоминание вместо вопроса — просят о том же.
        st["pending_hits"] += 1
        text = pending_text(st, cur, m["window"], sid)
    if cur < m["base"]:
        # Отложенный порог и постановка раскладки — с нуля. Файл с промптом не удалять: по нему
        # claude-compact --resend присылает запрос заново, и модель читает файл уже после этого хука.
        st.update(next=None, extract_pending_at=None)
        write_mark(sid, st, m)
    else:
        # Отсрочка — пометкой в прошлом ответе: вызывать `defer` не нужно (в режиме auto классификатор
        # блокирует его как самомодификацию, проверено 29.09.2026).
        n = deferred_in_last_answer(inp.get("transcript_path"))
        if n and n > (st["next"] or 0):
            set_next(st, n, m["hard"])
        write_mark(sid, st, m)
        if text is None and (cur >= m["hard"] or cur >= (st["next"] or m["base"])):
            if not os.environ.get("HERDR_PANE_ID") and st["sorted_at"] and unsorted(st, cur) <= MIN_UNSORTED:
                text = compact_waits(cur, sid)
            else:
                text = question(sid, prompt, cur, m)
    if text:
        emit_context("UserPromptSubmit", text)


def on_tool(inp, st, sid):
    tool_input = inp.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    command = command if isinstance(command, str) else ""
    keep = KEEP_CMD.search(command)
    marker = not keep and MARKER_CMD.search(command)
    # Замер — только когда он нужен: хук висит на каждом вызове инструмента.
    if not (keep or marker or st["pending"] or st["baseline"] is None):
        return
    m = measure(inp)
    cur = m["tokens"]
    if cur is None:
        return
    note_baseline(st, m)
    st["tokens"] = cur
    if keep:
        emit_context("PostToolUse", request_keep(st, sid, m, keep))
    elif marker:
        was = st["pending"]
        st.update(sorted_at=cur, pending=None, pending_hits=0, manual_blocked=False, auto_blocks=0)
        if was == "auto":
            emit_context("PostToolUse", "context-guard: отметка принята, автосжатие пройдёт на следующем "
                                        "шаге. Продолжай прерванную работу.")
    elif st["pending"]:
        st["pending_hits"] += 1
        if st["pending_hits"] % PENDING_REPEAT == 1:
            emit_context("PostToolUse", pending_text(st, cur, m["window"], sid))


def request_keep(st, sid, m, match):
    """Переходное: `echo knowledge-keep-context <N> <зачем>` прежнего knowledge_guard — отсрочка проверки
    до N, как цитатой, только без шага STEP."""
    cur = st["tokens"]
    limit = min(m["hard"], KEEP_MAX) if KEEP_MAX else m["hard"]
    target = parse_tokens(match.group(1))
    if cur >= limit:
        return (f"context-guard: отсрочек больше нет — пройден предел {k(limit)}. Разложи незаписанное "
                f"({SKILL_TEXT}) и выполни `echo {MARKER}`.")
    if not target or target <= cur:
        return (f"context-guard: отсрочка не принята — нужна граница выше текущих {k(cur)}, например "
                f"`echo {KEEP} {k(min(cur + STEP, limit))} <зачем>`.")
    granted = set_next(st, target, limit, step=False)
    write_mark(sid, st, m)
    cut = f" (урезано до предела {k(limit)})" if granted < target else ""
    return (f"context-guard: отсрочка принята — следующая проверка контекста на {k(granted)}{cut}, там "
            "спрошу снова. Дальше откладывай цитатой «> контекст Nk, проверка отложена до Mk: <зачем>» в "
            "самом конце ответа. Продолжай работу.")


def passed(st, left, fresh):
    """Сжатие проходит: отложенное снято; без раскладки — запомнить, сколько не разложено (SessionStart)."""
    st.update(pending=None, manual_blocked=False, auto_blocks=0, compacted_unsorted=None if fresh else left,
              compact_started_at=time.time())


def on_precompact(inp, st, sid):
    m = measure(inp)
    if m["tokens"] is None:
        return  # без замера сжатию не мешать
    left = unsorted(st, m["tokens"])
    if os.environ.get("HERDR_PANE_ID"):
        precompact_herdr(inp, st, m, left)
    else:
        precompact_plain(inp, st, m, left)


def precompact_herdr(inp, st, m, left):
    """Панель herdr: раскладку ставим агенту в очередь, сжатие он вызовет сам (claude-compact)."""
    path = inp.get("transcript_path")
    extracted = extracted_since_compact(path)
    fresh = left <= MIN_UNSORTED or extracted
    if fresh:
        st["extract_pending_at"] = None
    if inp.get("trigger") == "auto":
        # Автосжатие ниже потолка отменяем: либо раскладка ещё нужна, либо она уже идёт или
        # закончилась, и тогда сожмёт claude-compact (иначе сжатий будет два, проверено 29.09.2026).
        # Claude Code повторяет попытку на каждом запросе; у потолка и после AUTO_MAX_BLOCKS отказов
        # пропускаем, чтобы сессия не упала.
        if m["tokens"] >= AUTO_CEILING * m["window"] or st["auto_blocks"] >= AUTO_MAX_BLOCKS:
            passed(st, left, fresh)
            return
        if extracted or is_extraction_running(path):
            st["auto_blocks"] += 1
            emit_json({"decision": "block", "reason": "Знания раскладываются, контекст сожмётся после "
                                                      "раскладки (context-guard)."})
            return
        if fresh:
            # Свежа только отметка `echo knowledge-sorted` без навыка: claude-compact никто не позовёт,
            # и отказ тянулся бы до AUTO_MAX_BLOCKS — знания разложены, сжатие проходит.
            passed(st, left, True)
            return
        st["auto_blocks"] += 1
    elif fresh:
        passed(st, left, fresh)
        return
    # Раскладку ставим в очередь один раз: при повторных попытках автосжатия только отменяем.
    if time.time() - (st["extract_pending_at"] or 0) > PENDING_TTL:
        st["extract_pending_at"] = time.time()
        custom = inp.get("custom_instructions")
        custom = " ".join(custom.split()) if isinstance(custom, str) else ""
        msg = (f"{SKILL} Перед сжатием контекста (context-guard): разложи знания, разбирая только то, что "
               f"появилось после прошлого сжатия, и в конце вызови ~/.claude/bin/claude-compact"
               + (f" -- {custom}" if custom else "") + ".")
        queue_extraction(os.environ["HERDR_PANE_ID"], msg)
    emit_json({"decision": "block", "reason": "Сначала раскладываю знания по местам, потом контекст "
                                              "сожмётся сам (context-guard)."})


def queue_extraction(pane, msg):
    """Промпт агенту в панель herdr после конца хода: процесс отвязан и переживает хук. Шлёт через
    claude-prompt слоя terminal, если он есть: тот прячет недописанный черновик владельца, иначе
    промпт дописался бы в его конец."""
    herdr = shutil.which("herdr") or str(Path.home() / ".local" / "bin" / "herdr")
    send = Path.home() / ".claude" / "bin" / "claude-prompt"
    send = [str(send)] if os.access(send, os.X_OK) else [herdr, "agent", "prompt"]
    try:
        subprocess.Popen(
            ["/bin/sh", "-c",
             'sleep 1; "$0" agent wait "$1" --until idle --until done --timeout 1800000 >/dev/null 2>&1 '
             '&& shift && "$@" >/dev/null 2>&1', herdr, pane, *send, pane, msg],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except (OSError, ValueError):
        pass


def precompact_plain(inp, st, m, left):
    """Без панели: сжать может только владелец — отказ кодом 2, модели — напоминания до отметки."""
    if left <= MIN_UNSORTED:
        passed(st, left, True)
        return
    if inp.get("trigger") == "auto":
        # Для автосжатия свежая раскладка — только отметка: вызов навыка в транскрипте значит и «раскладка
        # ещё идёт», а сжатие посреди неё сотрёт несделанное (в панели herdr такое автосжатие тоже ждёт).
        # У края окна и после AUTO_MAX_BLOCKS отказов — пропустить, чтобы сессия не упала.
        if m["window"] - m["tokens"] < AUTO_RESERVE or st["auto_blocks"] >= AUTO_MAX_BLOCKS:
            passed(st, left, False)
            return
        if st["pending"] != "auto":
            st.update(pending="auto", pending_at=time.time(), pending_hits=0)
        st["auto_blocks"] += 1
        block_stderr("context-guard: автосжатие отложено до выноса знаний.")
    if extracted_since_compact(inp.get("transcript_path")):
        passed(st, left, True)
        return
    if st["manual_blocked"]:  # владелец повторил /compact — не держим
        passed(st, left, False)
        return
    custom = inp.get("custom_instructions")
    st.update(pending="manual", pending_at=time.time(), pending_hits=0, manual_blocked=True,
              custom_instructions=custom if isinstance(custom, str) and custom.strip() else None)
    block_stderr("context-guard: сжатие отложено — сначала Claude разложит знания сессии по полочкам "
                 "(навык session-extract-knowledge) и напишет «жми /compact». Если он не начал сам, "
                 "отправьте любое сообщение. Повторный /compact пройдёт в любом случае.")


def on_rewake(sid):
    """asyncRewake: разбудить модель после отложенного /compact (сессия может простаивать)."""
    time.sleep(max(0.0, REWAKE_DELAY))  # дать основному обработчику PreCompact записать состояние
    st = read_state(sid)
    if (st and st["pending"] == "manual" and time.time() - st["pending_at"] < 60
            and st["rewoken_at"] != st["pending_at"]):
        st["rewoken_at"] = st["pending_at"]
        save(sid, st)
        block_stderr(pending_text(st, 0, 0, sid))


def on_session_start(inp, sid):
    source = inp.get("source")
    if source not in ("startup", "clear", "compact"):
        return None
    old = read_state(sid)
    st = fresh_state()
    left = old and old.get("compacted_unsorted")
    if source == "compact":
        # Без PreCompact (не было замера) — с запасом: сжатие идёт десятки секунд.
        note_compact(sid, (old or {}).get("compact_started_at") or time.time() - COMPACT_SLACK)
    if source == "compact" and left:
        emit_context("SessionStart", (
            f"context-guard: контекст сжат, а ~{k(left)} работы перед сжатием не разложены по "
            f"полочкам. Полная запись сессии цела: {inp.get('transcript_path')} (JSONL, сжатая часть — "
            "до последней строки с \"compact_boundary\"). В ближайшей удобной точке найди в ней "
            "grep'ом по ключевым словам решения, замеры, баги и грабли, которых нет в документации, "
            f"и запиши их ({SKILL_TEXT}); целиком запись не читать. {DONE}"))
    cleanup()
    return st


def say(stream, text):
    """Строка в stdout/stderr байтами UTF-8: консоль Windows иначе бьёт кириллицу."""
    stream.buffer.write((text + "\n").encode("utf-8"))
    stream.flush()


def defer_cli(sid, raw):
    n = parse_tokens(raw)
    if n is None:
        say(sys.stderr, f"не понял число токенов: {raw!r} (пример: 650000 или 650k)")
        sys.exit(1)
    st = load(sid)
    m = measure(sid=sid)
    cur = st["tokens"] or 0
    nxt = set_next(st, n, m["hard"])
    if nxt is None:
        say(sys.stderr, f"{k(n)} не больше текущего контекста {k(cur)}: отложить можно только на порог выше")
        sys.exit(1)
    save(sid, st)
    write_mark(sid, st, m)
    note = (f" (урезано до {k(nxt)}: дальше {k(STEP)} от текущего и выше {k(m['hard'])} нельзя)"
            if n > nxt else "")
    say(sys.stdout, f"следующая проверка контекста — на {k(nxt)}{note}")


HANDLERS = {"UserPromptSubmit": on_prompt, "PostToolUse": on_tool, "PreCompact": on_precompact}


def main(argv):
    if argv[:1] == ["defer"] and len(argv) == 3:
        defer_cli(argv[1], argv[2])
        return
    if argv not in ([], ["hook"], ["rewake"]):
        sys.exit(__doc__)
    if "1" in (os.environ.get("CONTEXT_GUARD_OFF"), os.environ.get("KNOWLEDGE_GUARD_OFF")):
        return
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        return  # сессии без человека (claude -p, Agent SDK) — не трогаем
    inp = read_input("CONTEXT_GUARD_DUMP")
    if not inp or inp.get("agent_id"):
        return  # субагент: у него своё сжатие и свой контекст
    sid = inp.get("session_id")
    if not isinstance(sid, str) or not sid:
        return
    event = inp.get("hook_event_name")
    if argv == ["rewake"]:
        # В панели herdr модель будит сама постановка раскладки: второе пробуждение лишнее.
        if event == "PreCompact" and inp.get("trigger") == "manual" and not os.environ.get("HERDR_PANE_ID"):
            on_rewake(sid)
        return
    if event == "SessionStart":
        st = on_session_start(inp, sid)
        if st is not None:
            save(sid, st)
        return
    handler = HANDLERS.get(event)
    if handler is None:
        return
    st = load(sid)
    before = dict(st)
    try:
        handler(inp, st, sid)
    finally:  # и при отказе кодом 2 (block_stderr) состояние записано
        if st != before:
            save(sid, st)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:  # без данных или на странном вводе хук молча пропускает работу
        pass
