"""Проверка limit-guard.py без Claude Code: запуск как хука на выдуманной сессии во временном каталоге.

Запуск: python hooks/test_limit_guard.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "limit-guard.py"
STUB = HERE.parent / "terminal" / "hooks" / "limit-guard.py"
DAY = 86400
POSIX = os.name != "nt"
RESUME = "Лимит сброшен (limit-guard): продолжай работу по плану"


class Session:
    """Выдуманная сессия: подменённые HOME, USERPROFILE и CLAUDE_CONFIG_DIR, лимиты и вызовы хука."""

    def __init__(self, tmp, sid="s1"):
        self.tmp = Path(tmp)
        self.sid = sid
        self.claude = self.tmp / ".claude"
        self.status = self.claude / "session-status"
        self.guard = self.claude / "limit-guard"
        self.status.mkdir(parents=True)
        self.guard.mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.tmp), "USERPROFILE": str(self.tmp),
                    "CLAUDE_CONFIG_DIR": str(self.claude)}
        for key in ("LIMIT_GUARD_5H", "LIMIT_GUARD_7D", "LIMIT_GUARD_GRACE", "LIMIT_GUARD_DUMP"):
            self.env.pop(key, None)
        self.now = time.time()
        self.reset5 = int(self.now + 3600)

    # --- источники ---

    def limits(self, five=None, seven=None, ts=None, reset5=None, reset7=None):
        """rate-limits.json статусной строки: пятичасовой и недельный лимиты в процентах."""
        data = {"ts": ts or self.now}
        if five is not None:
            data["five_hour"] = {"used_percentage": five, "resets_at": reset5 or self.reset5}
        if seven is not None:
            data["seven_day"] = {"used_percentage": seven, "resets_at": reset7 or int(self.now + 3 * DAY)}
        (self.status / "rate-limits.json").write_text(json.dumps(data), encoding="utf-8")

    def mod_limits(self, sid, five, at=None):
        """Файл мода другой сессии с пятичасовым лимитом."""
        (self.status / f"{sid}.state.json").write_text(json.dumps({"v": 1, "rate_limits": {"five_hour": {
            "used_percentage": five, "resets_at": self.reset5, "at": at or self.now}}}), encoding="utf-8")

    def touch(self, name, age):
        """Файл в limit-guard/ с mtime «age секунд назад»."""
        path = self.guard / name
        path.write_text("x", encoding="utf-8")
        t = self.now - age
        os.utime(path, (t, t))
        return path

    def state(self):
        path = self.guard / f"{self.sid}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def set_state(self, reset=None, started=None):
        (self.guard / f"{self.sid}.json").write_text(json.dumps(
            {"reset": reset or self.reset5, "started": started if started is not None else self.now - 3600}),
            encoding="utf-8")

    # --- вызовы ---

    def run(self, script, raw):
        r = subprocess.run([sys.executable, str(script)], input=raw, capture_output=True, env=self.env,
                           timeout=30)
        return r.returncode, r.stdout, r.stderr.decode("utf-8")

    def call(self, event, script=HOOK, **fields):
        inp = {"session_id": self.sid, "cwd": str(self.tmp), "hook_event_name": event, **fields}
        code, out, err = self.run(script, json.dumps(inp).encode())
        hso = json.loads(out)["hookSpecificOutput"] if out.strip() else None
        return code, hso, err

    def tool(self, script=HOOK, **fields):
        """PreToolUse: (код, причина отказа или None, stderr)."""
        code, hso, err = self.call("PreToolUse", script, tool_name="Bash", tool_input={"command": "ls"}, **fields)
        if hso is None:
            return code, None, err
        assert hso["permissionDecision"] == "deny", hso
        return code, hso["permissionDecisionReason"], err

    def prompt(self, text="продолжим", script=HOOK, **fields):
        """UserPromptSubmit: (код, additionalContext или None, stderr)."""
        code, hso, err = self.call("UserPromptSubmit", script, prompt=text, **fields)
        return code, hso["additionalContext"] if hso else None, err


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def denied(self, **kw):
        code, reason, err = self.s.tool(**kw)
        self.assertEqual((code, err), (0, ""))
        self.assertIsNotNone(reason)
        return reason

    def silent_tool(self, **kw):
        self.assertEqual(self.s.tool(**kw), (0, None, ""))

    def test_first_action_denied_with_instruction(self):
        s = self.s
        s.limits(five=95)
        reason = self.denied()
        self.assertIn("пятичасовой лимит использован на 95%", reason)
        for part in ("/session-extract-knowledge", "CronCreate", "recurring: false", "Лимит сброшен (limit-guard)",
                     str(s.guard / "s1.resume.md"), "На всё это есть 10 мин"):
            self.assertIn(part, reason)
        self.assertRegex(reason, r'cron "\d+ \d+ \d+ \d+ \*"')
        st = s.state()
        self.assertEqual(st["reset"], s.reset5)
        self.assertAlmostEqual(st["started"], s.now, delta=30)
        self.assertEqual([p.name for p in s.guard.glob("*.tmp.*")], [])

    def test_grace_then_hard_deny(self):
        s = self.s
        s.limits(five=95)
        self.denied()
        self.silent_tool()  # идут GRACE минут на раскладку знаний
        s.set_state(started=s.now - 11 * 60)
        reason = self.denied()
        self.assertIn("сессия на паузе до", reason)
        self.assertIn("закончи ход одной строкой", reason)
        self.assertNotIn("CronCreate", reason)

    def test_grace_from_env(self):
        s = self.s
        s.env["LIMIT_GUARD_GRACE"] = "1"
        s.limits(five=95)
        self.denied()
        s.set_state(started=s.now - 90)
        self.assertIn("на паузе до", self.denied())

    def test_below_threshold_is_silent_and_env_thresholds(self):
        s = self.s
        s.limits(five=89, seven=89)
        self.silent_tool()
        self.assertEqual(s.prompt(), (0, None, ""))
        self.assertIsNone(s.state())
        s.env["LIMIT_GUARD_5H"] = "50"
        self.assertIn("пятичасовой", self.denied())

    def test_both_over_waits_for_later_reset(self):
        s = self.s
        s.limits(five=95, seven=92)
        reason = self.denied()
        self.assertIn("недельный лимит использован на 92%", reason)
        self.assertEqual(s.state()["reset"], int(s.now + 3 * DAY))

    def test_prompt_during_pause(self):
        s = self.s
        s.limits(five=95)
        code, ctx, err = s.prompt()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Поставь работу на паузу", ctx)
        self.assertIn("явно просит работать несмотря на лимит — выполни просьбу вместо паузы", ctx)
        s.set_state(started=s.now - 3600)
        ctx = s.prompt("ну что там")[1]
        self.assertIn("сессия на паузе до", ctx)
        self.assertNotIn("Поставь работу на паузу", ctx)
        self.assertAlmostEqual(s.state()["started"], s.now, delta=30)  # человек рядом — снова GRACE
        self.silent_tool()

    def test_resume_prompt_passes(self):
        s = self.s
        s.limits(five=95)
        self.assertEqual(s.prompt(RESUME), (0, None, ""))
        self.assertEqual(s.prompt("  " + RESUME), (0, None, ""))
        self.assertIsNone(s.state())

    def test_reset_passed_removes_state(self):
        s = self.s
        s.limits(five=95)
        self.denied()
        self.assertIsNotNone(s.state())
        s.limits(five=95, reset5=int(s.now - 60))  # сброс прошёл, нового показания пока нет
        self.silent_tool()
        self.assertIsNone(s.state())
        s.limits(five=2)
        self.silent_tool()

    def test_new_reset_is_new_pause(self):
        s = self.s
        s.limits(five=95)
        self.denied()
        s.limits(five=95, reset5=s.reset5 + 5 * 3600)  # следующее окно, снова упёрлись
        self.assertIn("CronCreate", self.denied())

    def test_same_window_with_rounding_difference_is_one_pause(self):
        s = self.s
        s.limits(five=95)
        self.denied()
        s.limits(five=95, reset5=s.reset5 + 1)  # другой источник округлил иначе
        self.silent_tool()

    def test_stale_reading_still_counts(self):
        s = self.s
        s.limits(five=95, ts=s.now - 3600)  # час назад, но сброс впереди
        self.assertIn("CronCreate", self.denied())

    def test_limits_from_other_session_file(self):
        s = self.s
        s.mod_limits("other", 93)
        self.assertIn("пятичасовой лимит использован на 93%", self.denied())

    def test_no_data_and_broken_input_are_silent(self):
        self.silent_tool()
        self.assertEqual(self.s.prompt(), (0, None, ""))
        self.s.limits(five=95)
        for raw in (b"", "{битый".encode(), b"[1]", b"null", b'{"hook_event_name": "PreToolUse"}',
                    b'{"session_id": "s1", "hook_event_name": "Stop"}', b'{"session_id": 7, "hook_event_name": "PreToolUse"}'):
            self.assertEqual(self.s.run(HOOK, raw), (0, b"", ""), raw)
        (self.s.status / "rate-limits.json").write_text("{битый", encoding="utf-8")
        self.silent_tool()

    def test_output_is_ascii_json(self):
        s = self.s
        s.limits(five=95)
        code, out, _ = s.run(HOOK, json.dumps({"session_id": "s1", "hook_event_name": "PreToolUse"}).encode())
        self.assertEqual(code, 0)
        out.decode("ascii")
        self.assertIn("пятичасовой", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_dump_of_input(self):
        s = self.s
        dump = s.tmp / "dump.txt"
        s.env["LIMIT_GUARD_DUMP"] = str(dump)
        s.tool()
        s.prompt("привет")
        lines = dump.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["hook_event_name"] for x in lines], ["PreToolUse", "UserPromptSubmit"])


class CoordinatorTest(unittest.TestCase):
    """Координатор жив — хук молчит; признак — mtime alive-файлов, не registry.md."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.s.limits(five=95)

    def tearDown(self):
        self._tmp.cleanup()

    def paused(self):
        code, reason, _ = self.s.tool()
        self.assertEqual(code, 0)
        return reason is not None

    def test_fresh_alive_limit_guard_silences(self):
        self.s.touch("alive-limit-guard", 10 * 60)
        self.assertFalse(self.paused())
        self.assertEqual(self.s.prompt(), (0, None, ""))
        self.assertIsNone(self.s.state())

    def test_old_alive_limit_guard_does_not(self):
        self.s.touch("alive-limit-guard", 40 * 60)
        self.assertTrue(self.paused())

    def test_alive_night_work_lasts_75_minutes(self):
        self.s.touch("alive-night-work", 60 * 60)
        self.assertFalse(self.paused())
        self.s.touch("alive-night-work", 80 * 60)
        self.assertTrue(self.paused())

    def test_night_work_term_is_not_for_limit_guard_file(self):
        self.s.touch("alive-limit-guard", 60 * 60)  # у навыка limit-guard срок 30 минут
        self.assertTrue(self.paused())

    def test_registry_is_ignored(self):
        self.s.touch("registry.md", 60)
        self.assertTrue(self.paused())


class SubagentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.s.limits(five=95)

    def tearDown(self):
        self._tmp.cleanup()

    def test_subagent_does_not_start_pause(self):
        self.assertEqual(self.s.tool(agent_id="a1"), (0, None, ""))
        self.assertIsNone(self.s.state())
        # основная сессия после этого получает инструкцию как обычно
        self.assertIn("CronCreate", self.s.tool()[1])

    def test_subagent_in_grace_is_silent(self):
        self.s.set_state(started=self.s.now - 60)
        self.assertEqual(self.s.tool(agent_id="a1"), (0, None, ""))

    def test_subagent_after_grace_is_denied(self):
        self.s.set_state(started=self.s.now - 3600)
        code, reason, _ = self.s.tool(agent_id="a1")
        self.assertEqual(code, 0)
        self.assertIn("сессия на паузе до", reason)
        self.assertNotIn("CronCreate", reason)

    def test_subagent_prompt_is_ignored(self):
        self.assertEqual(self.s.prompt(agent_id="a1"), (0, None, ""))
        self.assertIsNone(self.s.state())


class OffSwitchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.s.limits(five=95)

    def tearDown(self):
        self._tmp.cleanup()

    def test_off_silences_within_five_hour_window(self):
        off = self.s.touch("off", 60)
        self.assertEqual(self.s.tool(), (0, None, ""))
        self.assertEqual(self.s.prompt(), (0, None, ""))
        self.assertTrue(off.exists())

    def test_off_from_previous_window_removes_itself(self):
        off = self.s.touch("off", 6 * 3600)  # окно началось час назад: off старше его начала
        code, reason, _ = self.s.tool()
        self.assertEqual(code, 0)
        self.assertIn("CronCreate", reason)
        self.assertFalse(off.exists())

    def test_off_without_window_reading_stays(self):
        off = self.s.touch("off", 6 * 3600)
        self.s.limits(seven=95)
        self.assertEqual(self.s.tool(), (0, None, ""))
        self.assertTrue(off.exists())


@unittest.skipUnless(POSIX, "заглушка перенаправляет через os.execv")
class StubTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.s.limits(five=95)

    def tearDown(self):
        self._tmp.cleanup()

    def test_stub_forwards_stdin_to_new_hook(self):
        code, reason, err = self.s.tool(script=STUB)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("пятичасовой лимит использован на 95%", reason)
        self.assertIsNotNone(self.s.state())
        self.assertIn("сессия на паузе до", self.s.prompt(script=STUB, text="x")[1] or "")

    def test_stub_without_new_hook_exits_silently(self):
        kit = Path(self._tmp.name) / "kit"
        (kit / "terminal" / "hooks").mkdir(parents=True)
        copy = kit / "terminal" / "hooks" / "limit-guard.py"
        shutil.copy(STUB, copy)
        self.assertEqual(self.s.tool(script=copy), (0, None, ""))
        self.assertIsNone(self.s.state())


if __name__ == "__main__":
    unittest.main()
