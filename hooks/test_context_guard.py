"""Проверка context-guard.py без Claude Code: запуск как хука на выдуманной сессии во временном каталоге.

Запуск: python hooks/test_context_guard.py; CONTEXT_GUARD_IMPL=node — то же для context-guard.mjs.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPL = os.environ.get("CONTEXT_GUARD_IMPL", "")
HOOK = HERE / ("context-guard.mjs" if IMPL == "node" else "context-guard.py")
NODE = shutil.which("node") or "node"
KG_STUB = HERE / "knowledge_guard.py"
TERMINAL_STUB = HERE.parent / "terminal" / "hooks" / "context-guard.py"
MODEL = "claude-opus-5-5"
HAIKU = "claude-haiku-4-5-20251001"
POSIX = os.name != "nt"
DAY = 86400
QUEUED = "Сначала раскладываю знания по местам, потом контекст сожмётся сам (context-guard)."
RUNNING = "Знания раскладываются, контекст сожмётся после раскладки (context-guard)."


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class Session:
    """Выдуманная сессия: подменённые HOME, USERPROFILE и CLAUDE_CONFIG_DIR, запись JSONL и вызовы хука."""

    def __init__(self, tmp, sid="s1"):
        self.tmp = Path(tmp)
        self.sid = sid
        self.safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
        self.claude = self.tmp / ".claude"
        self.status = self.claude / "session-status"
        self.guard = self.claude / "context-guard"
        self.transcript = self.tmp / f"{self.safe}.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("CONTEXT_GUARD_", "KNOWLEDGE_GUARD_", "HERDR_"))}
        for key in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
                    "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_EFFORT", "PYTHONIOENCODING"):
            self.env.pop(key, None)
        self.env.update(HOME=str(self.tmp), USERPROFILE=str(self.tmp), CLAUDE_CONFIG_DIR=str(self.claude),
                        CLAUDE_CODE_ENTRYPOINT="cli", CONTEXT_GUARD_REWAKE_DELAY="0")

    # --- запись сессии ---

    def append(self, entry):
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": now_iso(), **entry}) + "\n")

    def at(self, tokens, model=MODEL, text=None, **extra):
        """Ответ модели, после которого в контексте `tokens` токенов (вход и кэш; выход не в счёт)."""
        message = {"model": model, "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                              "cache_read_input_tokens": tokens - 1002, "output_tokens": 500}}
        if text is not None:
            message["content"] = [{"type": "text", "text": text}]
        self.append({"type": "assistant", "isSidechain": False, **extra, "message": message})

    def user(self, text="дальше"):
        self.append({"type": "user", "message": {"role": "user", "content": text}})

    def skill(self, name="session-extract-knowledge"):
        self.append({"type": "assistant", "message": {"model": MODEL, "content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": name}}]}})

    def boundary(self, post=19_000):
        self.append({"type": "system", "subtype": "compact_boundary", "compactMetadata": {"postTokens": post}})

    def settings(self, **data):
        self.claude.mkdir(parents=True, exist_ok=True)
        (self.claude / "settings.json").write_text(json.dumps(data), encoding="utf-8")

    def mod(self, **context):
        """Файл мода session-state: модель и контекст, снятые сейчас."""
        self.status.mkdir(parents=True, exist_ok=True)
        t = time.time()
        (self.status / f"{self.safe}.state.json").write_text(json.dumps({
            "v": 1, "session_id": self.sid, "ts": t, "model": {"id": MODEL, "at": t},
            "context": {"window": 1_000_000, "at": t, **context}}), encoding="utf-8")

    # --- вызовы ---

    def run(self, script, raw, *args):
        exe = NODE if Path(script).suffix == ".mjs" else sys.executable
        r = subprocess.run([exe, str(script), *args], input=raw, capture_output=True,
                           env=self.env, timeout=30)
        return r.returncode, r.stdout, r.stderr.decode("utf-8")

    def hook(self, event, *args, script=HOOK, **fields):
        """(код, JSON из stdout или None, stderr)."""
        inp = {"session_id": self.sid, "transcript_path": str(self.transcript), "cwd": str(self.tmp),
               "hook_event_name": event, **fields}
        code, out, err = self.run(script, json.dumps(inp).encode(), *args)
        return code, (json.loads(out) if out.strip() else None), err

    def ctx(self, event, *args, **fields):
        """additionalContext ответа хука (None — промолчал); падает, если хук отказал или писал в stderr."""
        code, out, err = self.hook(event, *args, **fields)
        if (code, err) != (0, ""):
            raise AssertionError(f"{event}: код {code}, stderr {err!r}")
        return out["hookSpecificOutput"]["additionalContext"] if out else None

    def prompt(self, text="сделай задачу", **fields):
        return self.ctx("UserPromptSubmit", prompt=text, **fields)

    def tool(self, command="ls"):
        return self.ctx("PostToolUse", tool_name="Bash", tool_input={"command": command})

    def compact(self, trigger="manual", *args, **fields):
        return self.hook("PreCompact", *args, trigger=trigger, **fields)

    def state(self):
        return json.loads((self.guard / f"{self.safe}.json").read_text(encoding="utf-8"))

    def patch_state(self, **fields):
        path = self.guard / f"{self.safe}.json"
        path.write_text(json.dumps({**self.state(), **fields}), encoding="utf-8")

    def mark(self):
        return json.loads((self.status / f"{self.safe}.ctx-mark").read_text(encoding="utf-8"))["at"]

    # --- herdr ---

    def fake_herdr(self):
        """herdr в PATH: пишет аргументы в файл и отвечает успехом; HERDR_PANE_ID=p1."""
        self.bin.mkdir(exist_ok=True)
        exe = self.bin / "herdr"
        exe.write_text("#!/bin/sh\necho \"$@\" >> \"$HERDR_LOG\"\necho '{\"result\": {}}'\n", encoding="utf-8")
        exe.chmod(0o755)
        self.env.update(PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        HERDR_LOG=str(self.tmp / "herdr.log"), HERDR_PANE_ID="p1")

    def herdr_calls(self, wait_for=0, timeout=6.0):
        """Вызовы herdr; wait_for — дождаться стольких (постановка идёт в фоне через секунду)."""
        log = self.tmp / "herdr.log"
        deadline = time.time() + timeout
        while True:
            calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
            if len(calls) >= wait_for or time.time() > deadline:
                return calls
            time.sleep(0.1)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.assertIsNone(self.s.ctx("SessionStart", source="startup"))

    def tearDown(self):
        self._tmp.cleanup()

    def started(self, tokens):
        """База 60k на первом промпте, затем ответ на `tokens`."""
        self.s.at(60_000)
        self.assertIsNone(self.s.prompt())
        self.s.at(tokens)


class PromptTest(Base):
    """Вопрос на старте промпта, отсрочки и пороги — общее для обеих концовок (здесь — без панели)."""

    def test_below_base_is_silent_and_marks(self):
        s = self.s
        s.at(120_000)
        self.assertIsNone(s.prompt())
        self.assertEqual(s.mark(), 300_000)
        self.assertEqual(s.state()["baseline"], 120_000)

    def test_question_without_pane(self):
        s = self.s
        self.started(350_000)
        ctx = s.prompt("почини тесты")
        self.assertTrue(ctx.startswith("Проверка контекста (context-guard): в контексте 350k токенов. По "
                                       "умолчанию — сжать: вызови скилл /session-extract-knowledge (отчёт — "
                                       "одной строкой), затем выполни запрос, а последней строкой ответа"), ctx)
        self.assertIn("попроси владельца набрать `/compact <инструкция>` — инструкция одной строкой до 250 "
                      "символов", ctx)
        self.assertIn("пути к разложенным файлам. Не сжимать", ctx)
        self.assertIn("не дальше 500k (хук дальше не пустит)", ctx)
        self.assertIn("например «до 400k»", ctx)
        self.assertTrue(ctx.endswith("На 900k отложить будет нельзя."), ctx)
        self.assertNotIn("claude-compact", ctx)
        self.assertNotIn("придёт снова", ctx)
        self.assertFalse((s.guard / "s1.prompt.md").exists())
        self.assertEqual(s.mark(), 300_000)

    def test_sorted_without_pane_only_reminds_compact(self):
        s = self.s
        self.started(350_000)
        self.assertIn("вызови скилл /session-extract-knowledge", s.prompt())
        s.at(360_000)
        self.assertIsNone(s.tool("echo knowledge-sorted"))
        s.at(370_000)
        ctx = s.prompt()
        self.assertTrue(ctx.startswith("Проверка контекста (context-guard): в контексте 370k токенов, знания "
                                       "уже разложены — сжатия ещё не было. Раскладку не повторяй"), ctx)
        self.assertIn("напомни владельцу набрать `/compact", ctx)
        self.assertNotIn("вызови скилл", ctx)
        s.at(390_000)
        self.assertIn("вызови скилл /session-extract-knowledge", s.prompt())

    def mod(self, ok=True):
        self.s.guard.mkdir(parents=True, exist_ok=True)
        (self.s.guard / "s1.compact-mod").write_text(json.dumps({"v": 1, "ok": ok}), encoding="utf-8")

    def test_question_with_compact_mod(self):
        # без панели, но с модом context-compact — как с панелью: сигнал моду и запрос заново
        s = self.s
        self.mod()
        self.started(350_000)
        ctx = s.prompt("сделай X")
        self.assertIn("к запросу не приступай", ctx)
        self.assertIn(f'`echo "context-compact --resend {s.guard / "s1.prompt.md"} -- <инструкция>"`', ctx)
        self.assertIn("придёт снова", ctx)
        self.assertNotIn("claude-compact", ctx)
        self.assertEqual((s.guard / "s1.prompt.md").read_text(encoding="utf-8"), "сделай X")
        s.at(360_000)
        s.tool("echo knowledge-sorted")
        s.at(370_000)
        self.assertIn('`echo "context-compact -- <инструкция>"`', s.prompt())

    def test_compact_mod_failed_asks_owner(self):
        self.mod(ok=False)
        self.started(350_000)
        ctx = self.s.prompt()
        self.assertIn("попроси владельца набрать `/compact", ctx)
        self.assertNotIn("context-compact", ctx)

    def test_hard_limit_without_pane(self):
        self.started(920_000)
        ctx = self.s.prompt()
        self.assertTrue(ctx.startswith("Проверка контекста (context-guard): в контексте 920k токенов — это "
                                       "предел 900k, откладывать нельзя. Прежде чем выполнять этот запрос, "
                                       "вызови скилл /session-extract-knowledge"), ctx)
        self.assertIn("`/compact <инструкция>`", ctx)

    def test_extract_prompt_is_not_questioned(self):
        self.started(350_000)
        for text in ("/session-extract-knowledge", "  /kit:session-end сегодня всё"):
            self.assertIsNone(self.s.prompt(text), text)
        self.assertIsNotNone(self.s.prompt("расскажи про /session-end"))

    def test_deferral_quote_moves_next(self):
        s = self.s
        self.started(340_000)
        s.at(350_000, text="Готово.\n_03.10.2026 16:40 МСК_\n> контекст 350k, проверка отложена до 401k: отладка")
        self.assertIsNone(s.prompt())
        self.assertEqual(s.state()["next"], 410_000)  # до 10k вверх
        self.assertEqual(s.mark(), 410_000)
        s.user()
        s.at(405_000)
        self.assertIsNone(s.prompt())
        s.user()
        s.at(415_000)
        self.assertIn("в контексте 415k токенов", s.prompt())
        s.user()
        s.at(420_000, text="> контекст 420k, проверка отложена до 900k: всё нужно")
        self.assertIsNone(s.prompt())
        self.assertEqual(s.state()["next"], 570_000)  # не дальше STEP от текущего
        s.user()
        s.at(580_000, text="Пометка пишется так: `> контекст 580k, проверка отложена до 700k`.\n"
                           "- > контекст 580k, проверка отложена до 700k")
        self.assertIn("в контексте 580k токенов", s.prompt())
        self.assertEqual(s.state()["next"], 570_000)

    def test_below_base_resets_deferral(self):
        s = self.s
        self.started(350_000)
        s.at(350_000, text="> контекст 350k, проверка отложена до 450k: отладка")
        s.prompt()
        self.assertEqual(s.mark(), 450_000)
        s.boundary(30_000)  # без SessionStart: сброс — по самому замеру
        self.assertIsNone(s.prompt())
        self.assertIsNone(s.state()["next"])
        self.assertEqual(s.mark(), 300_000)

    def test_defer_cli(self):
        s = self.s
        self.started(350_000)
        s.prompt()
        code, out, err = s.run(HOOK, b"", "defer", "s1", "450k")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.decode("utf-8").strip(), "следующая проверка контекста — на 450k")
        code, out, _ = s.run(HOOK, b"", "defer", "s1", "1m")
        self.assertEqual(out.decode("utf-8").strip(), "следующая проверка контекста — на 500k (урезано до 500k: "
                                                      "дальше 150k от текущего и выше 900k нельзя)")
        self.assertEqual((s.state()["next"], s.mark()), (500_000, 500_000))
        code, _, err = s.run(HOOK, b"", "defer", "s1", "300k")
        self.assertEqual(code, 1)
        self.assertIn("не больше текущего контекста 350k", err)
        code, _, err = s.run(HOOK, b"", "defer", "s1", "много")
        self.assertEqual(code, 1)
        self.assertIn("не понял число токенов", err)

    def test_keep_context_echo_is_deferral(self):
        s = self.s
        self.started(350_000)
        self.assertIsNotNone(s.prompt())
        self.assertIn("отсрочка не принята — нужна граница выше текущих 350k, например "
                      "`echo knowledge-keep-context 500k <зачем>`", s.tool("echo knowledge-keep-context 300k"))
        ctx = s.tool('echo knowledge-keep-context 700k "держу 40 файлов разбора"')
        self.assertTrue(ctx.startswith("context-guard: отсрочка принята — следующая проверка контекста на 700k, "
                                       "там спрошу снова."), ctx)  # без шага STEP, как прежняя отсрочка
        self.assertIn("«> контекст Nk, проверка отложена до Mk: <зачем>»", ctx)
        self.assertEqual(s.mark(), 700_000)
        s.at(600_000)
        self.assertIsNone(s.prompt())
        self.assertIn("урезано до предела 900k", s.tool("echo knowledge-keep-context 1.2m ещё"))
        s.at(905_000)
        self.assertIn("отсрочек больше нет — пройден предел 900k", s.tool("echo knowledge-keep-context 950k"))

    def test_keep_max_from_old_variable(self):
        s = self.s
        s.env["KNOWLEDGE_GUARD_KEEP_MAX"] = "600k"
        self.started(350_000)
        self.assertIn("на 600k (урезано до предела 600k)", s.tool("echo knowledge-keep-context 800k"))

    def test_thresholds_with_auto_compact_window_500k(self):
        s = self.s
        s.settings(autoCompactWindow=500_000)  # порог 467k: HARD_eff 467k, BASE_eff 300k
        self.started(320_000)
        ctx = s.prompt()
        self.assertIn("не дальше 467k", ctx)
        self.assertIn("На 467k отложить будет нельзя.", ctx)
        s.at(470_000)
        self.assertIn("— это предел 467k, откладывать нельзя", s.prompt())
        self.assertEqual(s.mark(), 300_000)

    def test_200k_model_thresholds(self):
        s = self.s
        s.at(20_000, model=HAIKU)
        self.assertIsNone(s.prompt())
        s.at(90_000, model=HAIKU)  # окно 200k: порог 167k, BASE_eff 100k
        self.assertIsNone(s.prompt())
        self.assertEqual(s.mark(), 100_200)
        s.at(120_000, model=HAIKU)
        ctx = s.prompt()
        self.assertIn("в контексте 120k токенов. По умолчанию — сжать", ctx)
        self.assertIn("не дальше 167k", ctx)
        s.at(170_000, model=HAIKU)
        self.assertIn("это предел 167k", s.prompt())

    def test_mod_auto_compact_at_sets_thresholds(self):
        s = self.s
        s.mod(tokens=350_000, auto_compact_at=465_000)
        self.assertIn("не дальше 465k", s.prompt())
        s.mod(tokens=470_000, auto_compact_at=465_000)
        self.assertIn("это предел 465k", s.prompt())

    def test_auto_compact_disabled_keeps_hard(self):
        s = self.s
        s.mod(tokens=600_000, auto_compact_at=None, auto_compact_enabled=False)
        self.assertIn("не дальше 750k", s.prompt())
        s.mod(tokens=920_000, auto_compact_at=None, auto_compact_enabled=False)
        self.assertIn("это предел 900k", s.prompt())

    def test_auto_compact_disabled_200k_window_caps_hard(self):
        s = self.s  # автосжатие выключено, окно 200k: предел — потолок окна (184k), BASE_eff 110k
        s.mod(tokens=105_000, window=200_000, auto_compact_at=None, auto_compact_enabled=False)
        self.assertIsNone(s.prompt())
        self.assertEqual(s.mark(), 110_400)
        s.mod(tokens=150_000, window=200_000, auto_compact_at=None, auto_compact_enabled=False)
        self.assertIn("не дальше 184k", s.prompt())
        s.mod(tokens=190_000, window=200_000, auto_compact_at=None, auto_compact_enabled=False)
        self.assertIn("это предел 184k", s.prompt())

    def test_measure_skips_sidechain_synthetic_and_sees_boundary(self):
        s = self.s
        s.at(60_000)
        self.assertIsNone(s.tool())
        self.assertEqual(s.state()["baseline"], 60_000)
        s.at(500_000, isSidechain=True)
        s.append({"type": "assistant", "message": {"model": "<synthetic>", "usage": {
            "input_tokens": 0, "output_tokens": 0}}})
        self.assertIsNone(s.prompt())
        self.assertEqual(s.state()["tokens"], 60_000)
        s.boundary(19_000)
        s.ctx("SessionStart", source="compact")
        s.tool()
        self.assertIsNone(s.state()["baseline"])  # граница сжатия — не база
        s.at(80_000)
        s.tool()
        self.assertEqual(s.state()["baseline"], 80_000)

    def test_stop_is_silent(self):
        self.started(700_000)
        self.assertEqual(self.s.hook("Stop", stop_hook_active=False), (0, None, ""))


class PlainCompactTest(Base):
    """PreCompact без панели herdr: отказ кодом 2, rewake, напоминания до отметки."""

    def test_manual_compact_blocked_once_then_rewake_then_passes(self):
        s = self.s
        self.started(150_000)
        code, out, err = s.compact(custom_instructions="про тесты")
        self.assertEqual((code, out), (2, None))
        self.assertTrue(err.startswith("context-guard: сжатие отложено — сначала Claude разложит знания"), err)
        code, _, err = s.compact("manual", "rewake", custom_instructions="про тесты")
        self.assertEqual(code, 2)
        self.assertIn("context-guard: владелец вызвал /compact", err)
        self.assertIn("«можно /compact про тесты»", err)
        self.assertEqual(s.compact("manual", "rewake")[0], 0)  # второй раз не будит
        self.assertIn("владелец вызвал /compact", s.prompt("ну"))
        self.assertEqual(s.compact(), (0, None, ""))  # владелец настоял
        self.assertEqual(s.state()["compacted_unsorted"], 90_000)
        s.boundary()
        ctx = s.ctx("SessionStart", source="compact")
        self.assertIn("context-guard: контекст сжат, а ~90k работы перед сжатием не разложены", ctx)
        self.assertIn(str(s.transcript), ctx)
        self.assertIsNone(s.state()["compacted_unsorted"])

    def test_manual_compact_after_sort_passes(self):
        s = self.s
        self.started(150_000)
        self.assertEqual(s.compact()[0], 2)
        s.at(170_000)
        self.assertIsNone(s.tool("echo knowledge-sorted"))
        self.assertIsNone(s.state()["pending"])
        self.assertEqual(s.state()["sorted_at"], 170_000)
        self.assertEqual(s.compact(), (0, None, ""))
        s.boundary()
        self.assertIsNone(s.ctx("SessionStart", source="compact"))

    def test_first_prompt_after_compact_before_boundary_written(self):
        # 03.10.2026: промпт пришёл через 0,1 с после сжатия, границы в транскрипте ещё не было — хук
        # взял последний ответ до сжатия и спросил про 319k при 28k.
        s = self.s
        self.started(350_000)
        s.tool("echo knowledge-sorted")
        self.assertEqual(s.compact(), (0, None, ""))
        time.sleep(0.01)
        self.assertIsNone(s.ctx("SessionStart", source="compact"))
        self.assertIsNone(s.prompt())
        s.boundary(28_000)
        s.at(90_000)
        self.assertIsNone(s.prompt())
        self.assertEqual(s.state()["baseline"], 90_000)

    def test_skill_call_in_transcript(self):
        s = self.s
        self.started(150_000)
        s.skill()
        self.assertEqual(s.compact(), (0, None, ""))  # ручное — навык раскладки вызван, промптов нет
        s.user()
        s.at(160_000)
        self.assertEqual(s.compact()[0], 2)  # после навыка был промпт
        s.skill("kit:session-end")
        self.assertEqual(s.compact()[0], 0)
        s.user()
        s.at(170_000)
        s.user("<command-name>/session-extract-knowledge</command-name>")
        self.assertEqual(s.compact()[0], 0)
        # Автосжатию свежа только отметка: по транскрипту раскладка может ещё идти.
        self.assertEqual(s.compact("auto")[0], 2)
        s.tool("echo knowledge-sorted")
        self.assertEqual(s.compact("auto")[0], 0)

    def test_small_session_compacts_freely(self):
        self.started(75_000)
        self.assertEqual(self.s.compact(), (0, None, ""))

    def test_marker_needs_echo_command(self):
        s = self.s
        self.started(300_000)
        s.tool("grep -r knowledge-sorted .")
        self.assertIsNone(s.state()["sorted_at"])
        s.tool("git push && echo knowledge-sorted")
        self.assertEqual(s.state()["sorted_at"], 300_000)

    def test_auto_compact_waits_for_sort(self):
        s = self.s
        self.started(905_000)
        code, _, err = s.compact("auto")
        self.assertEqual((code, err), (2, "context-guard: автосжатие отложено до выноса знаний."))
        self.assertIn("context-guard: автосжатие контекста отложено — знания с последнего выноса не записаны "
                      "(~845k). Запас до края окна ~95k", s.tool())
        s.at(910_000)
        for _ in range(3):
            self.assertIsNone(s.tool())  # не на каждом вызове
        self.assertIn("автосжатие контекста отложено", s.tool())
        self.assertEqual(s.compact("auto")[0], 2)
        self.assertIn("автосжатие контекста отложено", s.prompt("ну"))
        self.assertEqual(s.hook("Stop", stop_hook_active=False), (0, None, ""))
        s.at(930_000)
        self.assertIn("отметка принята", s.tool("echo knowledge-sorted"))
        self.assertEqual(s.compact("auto"), (0, None, ""))

    def test_auto_compact_passes_near_window_edge(self):
        s = self.s
        self.started(965_000)  # до края 35k < запаса 40k
        self.assertEqual(s.compact("auto"), (0, None, ""))
        self.assertEqual(s.state()["compacted_unsorted"], 905_000)

    def test_auto_compact_gives_up_after_40_blocks(self):
        s = self.s
        self.started(600_000)
        self.assertEqual(s.compact("auto")[0], 2)
        s.patch_state(auto_blocks=39)
        self.assertEqual(s.compact("auto")[0], 2)
        self.assertEqual(s.state()["auto_blocks"], 40)
        self.assertEqual(s.compact("auto"), (0, None, ""))
        self.assertEqual((s.state()["auto_blocks"], s.state()["compacted_unsorted"]), (0, 540_000))

    def test_hook_installed_mid_session_counts_everything(self):
        s = Session(self._tmp.name, sid="old")  # без SessionStart
        s.at(150_000)
        self.assertEqual(s.compact()[0], 2)
        self.assertEqual(s.state()["baseline"], 0)

    def test_state_moves_from_knowledge_guard(self):
        s = Session(self._tmp.name, sid="home")
        old = s.claude / "knowledge-guard"
        old.mkdir(parents=True)
        (old / "home.json").write_text(json.dumps({
            "baseline": 100_000, "sorted_at": 380_000, "reminded": 75, "pending": "review",
            "keep_until": 600_000, "keep_reason": "разбор"}), encoding="utf-8")
        s.at(390_000)
        self.assertIsNone(s.prompt())  # отсрочка knowledge_guard — порог проверки
        st = s.state()
        self.assertEqual((st["baseline"], st["next"], st["pending"]), (100_000, 600_000, None))
        self.assertEqual(s.mark(), 600_000)
        self.assertEqual(s.compact(), (0, None, ""))  # с отметки 10k — без волны

    def test_old_context_guard_state_gets_baseline(self):
        s = Session(self._tmp.name, sid="mac")
        s.guard.mkdir(parents=True, exist_ok=True)
        (s.guard / "mac.json").write_text(json.dumps({"tokens": 350_000, "next": 450_000}), encoding="utf-8")
        s.at(360_000)
        self.assertIsNone(s.prompt())
        self.assertEqual((s.state()["baseline"], s.state()["next"]), (360_000, 450_000))
        s.at(370_000)
        self.assertEqual(s.compact(), (0, None, ""))

    def test_session_start_cleans_old_files(self):
        s = self.s
        old_files = [s.guard / "old.json", s.guard / "old.prompt.md", s.claude / "knowledge-guard" / "old.json",
                     s.status / "old.ctx-mark"]
        fresh = s.guard / "new.json"
        for f in old_files + [fresh]:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("{}", encoding="utf-8")
        t = time.time() - 31 * DAY
        for f in old_files:
            os.utime(f, (t, t))
        s.ctx("SessionStart", source="clear")
        self.assertEqual([f for f in old_files if f.exists()], [])
        self.assertTrue(fresh.exists())

    def test_resume_keeps_state(self):
        s = self.s
        self.started(150_000)
        s.compact()
        s.ctx("SessionStart", source="resume")
        self.assertEqual(s.state()["pending"], "manual")

    def test_state_numbers_read_like_int(self):
        """Токены в состоянии — как int() Python: True — 1, строка цифр — число; SessionStart не теряется."""
        s = self.s
        old = s.guard / "old.json"
        for left, shown in ((True, "~0k"), (" 25000 ", "~25k"), ("1_500_000", "~1500k")):
            old.write_text("{}", encoding="utf-8")
            t = time.time() - 31 * DAY
            os.utime(old, (t, t))
            s.patch_state(compacted_unsorted=left, tokens=5)
            ctx = s.ctx("SessionStart", source="compact")
            self.assertIn(f"а {shown} работы перед сжатием", ctx)
            self.assertEqual((s.state()["compacted_unsorted"], s.state()["tokens"]), (None, None))
            self.assertFalse(old.exists())
        s.patch_state(tokens=True)
        self.assertEqual(s.run(HOOK, b"", "defer", "s1", "0.001k"),
                         (1, b"", "0k не больше текущего контекста 0k: отложить можно только на порог выше\n"))


class InputTest(Base):
    def test_broken_input_is_silent(self):
        for raw in (b"", "{битый".encode(), b"[1, 2]", b"null", b'{"session_id": 5}', b'{"session_id": ""}'):
            self.assertEqual(self.s.run(HOOK, raw), (0, b"", ""), raw)

    def test_module_of_other_version_is_silent(self):
        """Модуля состояния нет или в нём нет нужного имени (набор обновлён наполовину) — тихий выход 0."""
        s = self.s
        kit = Path(self._tmp.name) / "kit"
        kit.mkdir()
        hook = kit / HOOK.name
        shutil.copy(HOOK, hook)
        module, define = (("session-state.mjs", b"export function parse_tokens(") if HOOK.suffix == ".mjs"
                          else ("session_state.py", b"def parse_tokens("))
        text = (HERE / module).read_bytes()
        self.assertIn(define, text)
        (kit / module).write_bytes(text.replace(define, define.replace(b"parse_tokens", b"parse_tokens_old")))
        self.started(350_000)
        for base in (None, "300k"):  # с CONTEXT_GUARD_BASE имя нужно ещё при загрузке хука
            if base:
                s.env["CONTEXT_GUARD_BASE"] = base
            self.assertEqual(s.hook("UserPromptSubmit", script=hook, prompt="x"), (0, None, ""), base)
        (kit / module).unlink()
        self.assertEqual(s.hook("UserPromptSubmit", script=hook, prompt="x"), (0, None, ""))
        self.assertIn("350k", s.prompt())  # целый набор на том же входе спрашивает

    def test_off_switches(self):
        s = self.s
        self.started(150_000)
        for name in ("CONTEXT_GUARD_OFF", "KNOWLEDGE_GUARD_OFF"):
            s.env[name] = "1"
            self.assertEqual(s.compact(), (0, None, ""), name)
            s.env.pop(name)
        self.assertIsNone(s.state()["pending"])
        self.assertEqual(s.compact()[0], 2)

    def test_subagent_and_sdk_ignored(self):
        s = self.s
        self.started(350_000)
        self.assertEqual(s.compact(agent_id="a1"), (0, None, ""))
        self.assertEqual(s.hook("UserPromptSubmit", prompt="x", agent_id="a1"), (0, None, ""))
        s.env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
        self.assertEqual(s.compact(), (0, None, ""))
        self.assertEqual(s.hook("UserPromptSubmit", prompt="x"), (0, None, ""))

    def test_no_data_is_silent(self):
        s = self.s
        self.assertEqual(s.hook("UserPromptSubmit", prompt="x"), (0, None, ""))
        self.assertEqual(s.compact(), (0, None, ""))
        s.transcript.unlink()
        self.assertEqual(s.compact("auto"), (0, None, ""))

    def test_unsafe_sid(self):
        s = Session(self._tmp.name, sid="a/b:c")
        s.at(60_000)
        s.prompt()
        s.at(350_000)
        self.assertIn("350k", s.prompt())
        self.assertTrue((s.guard / "a_b_c.json").exists())
        self.assertEqual(s.mark(), 300_000)

    def test_windows_encoding(self):
        """stdout — только ASCII (кириллица в JSON экранирована), stderr — байты UTF-8, вход — UTF-8."""
        s = self.s
        s.env["PYTHONIOENCODING"] = "ascii"
        self.started(350_000)
        inp = {"session_id": "s1", "transcript_path": str(s.transcript), "hook_event_name": "UserPromptSubmit",
               "prompt": "почини тесты"}
        code, out, err = s.run(HOOK, json.dumps(inp, ensure_ascii=False).encode("utf-8"))
        self.assertEqual((code, err), (0, ""))
        out.decode("ascii")
        self.assertIn("в контексте 350k", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        code, _, err = s.compact()
        self.assertEqual(code, 2)
        self.assertIn("сжатие отложено", err)


@unittest.skipUnless(POSIX, "фальшивый herdr — скрипт sh")
class HerdrTest(Base):
    """Концовка с панелью herdr: запрос заново через claude-compact, раскладка агенту, блок JSON."""

    def setUp(self):
        super().setUp()
        self.s.fake_herdr()

    def test_question_resends_prompt(self):
        s = self.s
        self.started(350_000)
        ctx = s.prompt("почини тесты")
        path = s.guard / "s1.prompt.md"
        self.assertTrue(ctx.startswith("Проверка контекста (context-guard): в контексте 350k токенов. По "
                                       "умолчанию — сжать: к запросу не приступай: вызови скилл "
                                       "/session-extract-knowledge"), ctx)
        self.assertIn(f"`~/.claude/bin/claude-compact --resend {path}`", ctx)
        self.assertIn("пути к разложенным файлам, а запрос придёт снова.", ctx)
        self.assertNotIn("попроси владельца", ctx)
        self.assertEqual(path.read_text(encoding="utf-8"), "почини тесты")
        s.at(920_000)
        self.assertIn("это предел 900k, откладывать нельзя. Прежде чем выполнять этот запрос, к запросу не "
                      "приступай", s.prompt())
        self.assertEqual(s.herdr_calls(), [])

    def test_resent_prompt_keeps_prompt_file(self):
        s = self.s
        self.started(350_000)
        s.prompt("почини тесты\nи CI")
        path = s.guard / "s1.prompt.md"
        s.boundary(30_000)
        s.ctx("SessionStart", source="compact")
        self.assertIsNone(s.prompt("Контекст сжат, знания разложены (context-guard). Выполни запрос "
                                   f"пользователя, пришедший до сжатия, — он в файле {path}"))
        self.assertEqual(path.read_text(encoding="utf-8"), "почини тесты\nи CI")
        self.assertEqual(s.mark(), 300_000)

    def test_manual_compact_blocks_and_queues_once(self):
        s = self.s
        self.started(350_000)
        for _ in range(2):
            code, out, err = s.compact(custom_instructions="про  тесты\nи CI")
            self.assertEqual((code, out, err), (0, {"decision": "block", "reason": QUEUED}, ""))
        self.assertEqual(s.herdr_calls(wait_for=2), [
            "agent wait p1 --until idle --until done --timeout 1800000",
            "agent prompt p1 /session-extract-knowledge Перед сжатием контекста (context-guard): разложи знания, "
            "разбирая только то, что появилось после прошлого сжатия, и в конце вызови "
            "~/.claude/bin/claude-compact -- про тесты и CI."])
        time.sleep(1.5)
        self.assertEqual(len(s.herdr_calls()), 2)  # второй отказ раскладку не ставит
        st = s.state()
        self.assertIsNone(st["pending"])
        self.assertIsNotNone(st["extract_pending_at"])

    def test_extraction_goes_through_claude_prompt(self):
        """Есть claude-prompt слоя terminal — промпт через него: он прячет недописанный черновик владельца."""
        s = self.s
        tool = s.tmp / ".claude" / "bin" / "claude-prompt"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("#!/bin/sh\necho claude-prompt \"$@\" >> \"$HERDR_LOG\"\n", encoding="utf-8")
        tool.chmod(0o755)
        self.started(350_000)
        s.compact()
        calls = s.herdr_calls(wait_for=2)
        self.assertEqual(calls[0], "agent wait p1 --until idle --until done --timeout 1800000")
        self.assertTrue(calls[1].startswith("claude-prompt p1 /session-extract-knowledge "), calls[1])

    def test_rewake_exits_at_once(self):
        s = self.s
        s.env["CONTEXT_GUARD_REWAKE_DELAY"] = "5"
        self.started(350_000)
        self.assertEqual(s.compact()[0], 0)
        t = time.time()
        self.assertEqual(s.compact("manual", "rewake"), (0, None, ""))
        self.assertLess(time.time() - t, 3)
        self.assertIsNone(s.state()["pending"])

    def test_fresh_sort_passes(self):
        s = self.s
        self.started(350_000)
        s.tool("echo knowledge-sorted")
        self.assertEqual(s.compact(), (0, None, ""))
        s.user()
        s.at(400_000)
        s.skill("kit:session-extract-knowledge")
        self.assertEqual(s.compact(), (0, None, ""))
        self.assertEqual(s.herdr_calls(), [])

    def test_auto_compact_blocks_until_ceiling_or_40(self):
        s = self.s
        s.settings(autoCompactWindow=500_000)
        self.started(470_000)
        self.assertEqual(s.compact("auto")[1], {"decision": "block", "reason": QUEUED})
        self.assertEqual(len(s.herdr_calls(wait_for=2)), 2)
        s.at(925_000)  # потолок — 92 % окна 1M
        self.assertEqual(s.compact("auto"), (0, None, ""))
        self.assertEqual(s.state()["compacted_unsorted"], 865_000)
        s.at(480_000)
        s.patch_state(auto_blocks=40)
        self.assertEqual(s.compact("auto"), (0, None, ""))

    def test_auto_compact_waits_while_extraction_runs(self):
        s = self.s
        s.settings(autoCompactWindow=500_000)
        self.started(470_000)
        s.skill()
        self.assertEqual(s.compact("auto")[1], {"decision": "block", "reason": RUNNING})
        s.tool("echo knowledge-sorted")
        self.assertEqual(s.compact("auto")[1], {"decision": "block", "reason": RUNNING})  # сожмёт claude-compact
        self.assertEqual(s.compact(), (0, None, ""))
        time.sleep(1.5)
        self.assertEqual(s.herdr_calls(), [])

    def test_auto_compact_after_marker_without_skill_passes(self):
        s = self.s  # отметка без навыка: claude-compact никто не позовёт — сжатие не держим
        s.settings(autoCompactWindow=500_000)
        self.started(450_000)
        s.tool("echo knowledge-sorted")
        s.at(465_000)
        self.assertEqual(s.compact("auto"), (0, None, ""))
        st = s.state()
        self.assertEqual((st["auto_blocks"], st["compacted_unsorted"]), (0, None))
        time.sleep(1.5)
        self.assertEqual(s.herdr_calls(), [])

    def test_tab_without_pane_is_plain(self):
        s = self.s
        s.env.pop("HERDR_PANE_ID")
        s.env["HERDR_TAB_ID"] = "t1"
        self.started(350_000)
        self.assertIn("попроси владельца", s.prompt())
        self.assertEqual(s.compact()[0], 2)


class StubTest(Base):
    """Заглушки по старым путям передают вход и аргументы в hooks/context-guard.py."""

    def prompt_via(self, script, *args):
        return self.s.hook("UserPromptSubmit", *args, script=script, prompt="почини тесты")

    def test_knowledge_guard_stub_forwards_stdin_and_args(self):
        s = self.s
        self.started(150_000)
        code, _, err = s.compact(script=KG_STUB, custom_instructions="про тесты")
        self.assertEqual(code, 2)
        self.assertIn("context-guard: сжатие отложено", err)
        code, _, err = s.compact("manual", "rewake", script=KG_STUB)
        self.assertEqual(code, 2)
        self.assertIn("«можно /compact про тесты»", err)
        s.at(350_000)
        self.assertEqual(self.prompt_via(KG_STUB), self.prompt_via(HOOK))

    def test_terminal_stub_forwards_hook_argument(self):
        self.started(350_000)
        via_stub = self.prompt_via(TERMINAL_STUB, "hook")
        self.assertIsNotNone(via_stub[1])
        self.assertEqual(via_stub, self.prompt_via(HOOK, "hook"))
        self.assertEqual(via_stub, self.prompt_via(HOOK))

    def test_stubs_without_new_hook_exit_silently(self):
        kit = Path(self._tmp.name) / "kit"
        (kit / "hooks").mkdir(parents=True)
        (kit / "terminal" / "hooks").mkdir(parents=True)
        self.started(350_000)
        for stub, copy in ((KG_STUB, kit / "hooks" / "knowledge_guard.py"),
                           (TERMINAL_STUB, kit / "terminal" / "hooks" / "context-guard.py")):
            shutil.copy(stub, copy)
            self.assertEqual(self.prompt_via(copy), (0, None, ""), copy)


if __name__ == "__main__":
    unittest.main()
