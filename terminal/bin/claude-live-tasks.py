#!/usr/bin/env python3
"""Фоновые задачи сессии Claude Code, которые ещё живы: по транскрипту.

    claude-live-tasks.py <session-id>   → {"tasks": [...], "wakeup": {...}|null}

Перезапуск claude (проверено 25.09.2026 на 2.1.282) убивает фоновые Bash-команды,
мониторы и фоновых субагентов и теряет будильник /loop (ScheduleWakeup); задания
CronCreate --resume восстанавливает сам. Чтобы вернуть остальное, claude-restart
берёт отсюда состояние до SIGTERM.

Живая задача = фоновый запуск (Bash с run_in_background или ушедший в фон
по таймауту, Monitor, Agent с run_in_background), по которому в транскрипте
ещё нет <task-notification> с конечным статусом, нет «Monitor expired», нет
вызова TaskStop и не вышел срок жизни (timeout_ms монитора, лимит фоновой Bash).
Без последних трёх проверок список копился за всю сессию: истечение монитора
и TaskStop не дают <status> (01.10.2026 — 82 задачи вместо 0).

Будильник /loop с давно прошедшим сроком не возвращается: если сессия была
свободна, он уже сработал, а его промпт в транскрипте не всегда совпадает
с текстом ScheduleWakeup (01.10.2026 каждый перезапуск слал /loop от 30.09).
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

TERMINAL = {"completed", "failed", "killed", "stopped", "timeout", "expired", "error", "cancelled"}
MONITOR_DEFAULT_MS, MONITOR_MAX_MS = 300_000, 1_800_000
BASH_BG_DEFAULT_MS, BASH_BG_MAX_MS = 1_800_000, 7_200_000
WAKEUP_STALE_S = 900  # будильник просрочен больше чем на 15 минут — считаем сработавшим


def ts_of(e):
    try:
        return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, AttributeError):
        return None


def lifetime_ms(use):
    """Сколько задача может прожить в фоне; None — без предела (субагент)."""
    inp = use["input"]
    if use["name"] == "Monitor":
        return min(inp.get("timeout_ms") or MONITOR_DEFAULT_MS, MONITOR_MAX_MS)
    if use["name"] == "Bash":
        if inp.get("run_in_background"):
            return min(inp.get("timeout") or BASH_BG_DEFAULT_MS, BASH_BG_MAX_MS)
        return BASH_BG_MAX_MS  # ушла в фон по таймауту — срок не известен, берём верхний
    return None


def transcript(sid):
    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    return max(paths, key=os.path.getmtime) if paths else None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def main():
    path = transcript(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path:
        print(json.dumps({"tasks": [], "wakeup": None}))
        return
    uses, started, started_at, finished = {}, {}, {}, set()
    wakeup, fired_after = None, []  # последний ScheduleWakeup и всё, что пришло в очередь после него
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            content = (e.get("message") or {}).get("content")
            if e.get("type") == "assistant" and isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_use" and c.get("name") in ("Bash", "Monitor", "Agent"):
                        uses[c["id"]] = c
                    if c.get("type") == "tool_use" and c.get("name") == "TaskStop":
                        inp = c.get("input") or {}
                        tid = inp.get("task_id") or inp.get("shell_id")
                        if tid:
                            finished.add(tid)
                    if c.get("type") == "tool_use" and c.get("name") == "ScheduleWakeup":
                        inp = c.get("input") or {}
                        wakeup = None
                        if not inp.get("stop") and inp.get("prompt"):
                            ts = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
                            delay = min(max(float(inp.get("delaySeconds") or 60), 60), 3600)
                            wakeup = {"due": int(ts + delay), "prompt": inp["prompt"]}
                        fired_after = []
            elif e.get("type") == "user" and isinstance(content, list):
                for c in content:
                    if c.get("type") != "tool_result" or c.get("tool_use_id") not in uses:
                        continue
                    use = uses[c["tool_use_id"]]
                    res = e.get("toolUseResult") if isinstance(e.get("toolUseResult"), dict) else {}
                    body = text_of(c.get("content"))
                    tid = res.get("backgroundTaskId") or res.get("taskId")
                    if not tid:
                        m = re.search(r"(?:with ID|task|\(ID): ?([a-z0-9]{6,})", body)
                        bg = use["input"].get("run_in_background") or use["name"] == "Monitor"
                        if m and (bg or "moved to the background" in body):
                            tid = m.group(1)
                    if not tid and use["name"] == "Agent" and use["input"].get("run_in_background"):
                        m = re.search(r"agentId: ([a-z0-9]+)", body)
                        tid = m and m.group(1)
                    if tid:
                        started[tid] = use
                        started_at[tid] = (ts_of(e), lifetime_ms(use))
            if e.get("type") == "queue-operation" and e.get("operation") == "enqueue":
                fired_after.append(e.get("content") or "")
            elif e.get("type") == "user" and isinstance(content, str):
                fired_after.append(content)
            # Уведомления о задачах приходят и как сообщения пользователя, и как queue-operation.
            raw = line if "<task-notification>" in line else ""
            if raw:
                for block in re.findall(r"<task-notification>(.*?)</task-notification>", raw.replace("\\n", "\n"), re.S):
                    st = re.search(r"<status>(\w+)</status>", block)
                    if (st and st.group(1) in TERMINAL) or "Monitor expired" in block:
                        finished.update(re.findall(r"<task-id>([^<]+)</task-id>", block))

    now = time.time()
    live = []
    for tid, use in started.items():
        if tid in finished:
            continue
        at, limit = started_at.get(tid, (None, None))
        if at and limit and now > at + limit / 1000:
            continue
        inp = use["input"]
        live.append({
            "task_id": tid,
            "tool": use["name"],
            "description": inp.get("description", ""),
            "command": inp.get("command") or inp.get("prompt", "")[:300],
            "timeout_ms": inp.get("timeout_ms"),
        })
    # Будильник уже сработал, если его промпт после вызова попал в очередь или в диалог.
    if wakeup and any(x.strip() == wakeup["prompt"].strip() for x in fired_after):
        wakeup = None
    if wakeup and now > wakeup["due"] + WAKEUP_STALE_S:
        wakeup = None
    print(json.dumps({"tasks": live, "wakeup": wakeup}, ensure_ascii=False))


if __name__ == "__main__":
    main()
