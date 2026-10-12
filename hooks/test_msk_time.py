"""Проверка msk_time.py без Claude Code: формат, разбор отметки, прощание, часы из конфига, опережение и отставание.

Запуск: python hooks/test_msk_time.py
"""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOOK = Path(__file__).with_name("msk_time.py")


def load_hook(clock):
    """Хук с часами из временного claude-kit.json (None — без ключа clock)."""
    tmp = tempfile.mkdtemp()
    Path(tmp, "claude-kit.json").write_text(json.dumps({"layers": [], **({"clock": clock} if clock else {})}), "utf-8")
    old = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = tmp
    try:
        spec = importlib.util.spec_from_file_location("msk_time", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            del os.environ["CLAUDE_CONFIG_DIR"]
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old


msk = load_hook({"utc_offset": 3, "label": "МСК", "farewell": "Пока."})

# 03.10.2026 16:40:30 МСК = 13:40:30 UTC
NOW = datetime(2026, 10, 3, 13, 40, 30, tzinfo=timezone.utc)


class HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state = msk.STATE_DIR
        msk.STATE_DIR = Path(self._tmp.name) / "msk-time"

    def tearDown(self):
        msk.STATE_DIR = self._state
        self._tmp.cleanup()

    def call(self, handler, now=NOW, **event):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            handler({"session_id": "s1", **event}, now)
        return json.loads(out.getvalue()) if out.getvalue() else None

    def stop(self, text, now=NOW, **event):
        return self.call(msk.on_stop, now, last_assistant_message=text, **event)

    # --- формат и разбор ---

    def test_format(self):
        self.assertEqual(msk.fmt(NOW), "03.10.2026 16:40 МСК")
        self.assertEqual(msk.stamp(NOW), "_03.10.2026 16:40 МСК_")

    def test_parse(self):
        got = msk.parse_stamp("Готово.\n\n_03.10.2026 16:40 МСК_")
        self.assertEqual(got, datetime(2026, 10, 3, 16, 40, tzinfo=msk.TZ))
        # из нескольких — последняя
        got = msk.parse_stamp("_01.10.2026 09:00 МСК_\nещё\n_03.10.2026 16:39 МСК_")
        self.assertEqual(got.minute, 39)
        for bad in ("🕒 03.10.2026 16:40 МСК, сб", "03.10.2026 16:40 МСК", "_31.02.2026 16:40 МСК_", ""):
            self.assertIsNone(msk.parse_stamp(bad), bad)

    # --- Stop ---

    def test_stop_accepts_fresh_stamp(self):
        self.assertIsNone(self.stop("Сделано.\n\n_03.10.2026 16:40 МСК_"))
        state = json.loads((msk.STATE_DIR / "s1.json").read_text("utf-8"))
        self.assertIn("last_stop", state)

    def test_stop_lag_and_ahead(self):
        for minute, blocked in ((36, False), (35, True), (41, False), (42, True)):
            got = self.stop(f"Ответ.\n_03.10.2026 16:{minute} МСК_")
            self.assertEqual(got is not None, blocked, minute)

    def test_stop_blocks_without_stamp(self):
        got = self.stop("Ответ без отметки.")
        self.assertEqual(got["decision"], "block")
        self.assertIn("_03.10.2026 16:40 МСК_", got["reason"])
        self.assertIsNone(self.stop("Ответ без отметки.", stop_hook_active=True))

    def test_stop_old_format_blocks(self):
        self.assertIsNotNone(self.stop("Ответ.\n🕒 03.10.2026 16:40 МСК, сб"))

    def test_stop_farewell(self):
        self.assertIsNone(self.stop("Пока."))
        self.assertIsNone(self.stop("Всё разложено.\n\n**Пока.**\n"))
        self.assertIsNotNone(self.stop("Пока. Но ещё вопрос?"))

    def test_clock_defaults(self):
        bare = load_hook(None)
        self.assertIsNone(bare.FAREWELL)
        self.assertFalse(bare.is_farewell("Пока."))
        self.assertTrue(bare.LABEL.startswith("UTC"), bare.LABEL)
        self.assertNotIn("прощание", bare.RULE)

    def test_clock_custom(self):
        other = load_hook({"utc_offset": -5, "farewell": "Пока."})
        self.assertEqual(other.LABEL, "UTC-5")
        self.assertEqual(other.fmt(NOW), "03.10.2026 08:40 UTC-5")
        self.assertIsNotNone(other.parse_stamp("_03.10.2026 08:40 UTC-5_"))
        self.assertTrue(other.is_farewell("Готово.\n\nПока."))
        self.assertEqual(load_hook({"utc_offset": 5.5}).LABEL, "UTC+5:30")

    def test_clock_empty_label(self):
        bare = load_hook({"utc_offset": 3, "label": ""})
        self.assertEqual(bare.fmt(NOW), "03.10.2026 16:40")
        self.assertIsNotNone(bare.parse_stamp("_03.10.2026 16:40_"))
        self.assertIsNone(bare.parse_stamp("_03.10.2026 16:40 МСК_"))
        self.assertIn("«_ДД.ММ.ГГГГ ЧЧ:ММ_»", bare.RULE)

    def test_stop_quote_after_stamp(self):
        text = "Сделано.\n\n_03.10.2026 16:40 МСК_\n> контекст 420k, проверка отложена до 500k: середина правки"
        self.assertIsNone(self.stop(text))

    # --- заметки ---

    def test_prompt_note(self):
        ctx = self.call(msk.on_prompt)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(ctx.startswith("Сейчас 03.10.2026 16:40 МСК."), ctx)
        self.assertIn("_ДД.ММ.ГГГГ ЧЧ:ММ МСК_", ctx)
        self.stop("_03.10.2026 16:40 МСК_")
        later = NOW + timedelta(hours=2, minutes=5)
        ctx = self.call(msk.on_prompt, later)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Прошлый ответ в этой сессии — 03.10.2026 16:40 МСК (2 ч 5 мин назад)", ctx)
        self.assertIn("Перерыв большой", ctx)
        self.stop("_03.10.2026 18:45 МСК_", later)
        ctx = self.call(msk.on_prompt, later + timedelta(minutes=30))["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("Перерыв большой", ctx)

    def mod(self, ok=True, sid="s1"):
        msk.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (msk.STATE_DIR / f"{sid}.mod").write_text(json.dumps({"v": 1, "session_id": sid, "ok": ok}), "utf-8")

    def test_mod_sets_time(self):
        # мод ставит время сам: метку не требовать, правило — не писать её
        self.mod()
        self.assertIsNone(self.stop("Готово."))
        ctx = self.call(msk.on_prompt)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("не пиши", ctx)
        self.assertNotIn("_ДД.ММ.ГГГГ ЧЧ:ММ МСК_", ctx)

    def test_mod_failed_or_other_session(self):
        # признак fail или чужой сессии — метку по-прежнему пишет модель
        self.mod(ok=False)
        self.assertEqual(self.stop("Готово.")["decision"], "block")
        self.mod(sid="s2")
        self.assertEqual(self.stop("Готово.")["decision"], "block")
        self.assertIn("_ДД.ММ.ГГГГ ЧЧ:ММ МСК_", self.call(msk.on_prompt)["hookSpecificOutput"]["additionalContext"])

    def test_tool_note_once_per_minute(self):
        self.assertIsNotNone(self.call(msk.on_tool))
        self.assertIsNone(self.call(msk.on_tool, NOW + timedelta(seconds=20)))
        self.assertIsNotNone(self.call(msk.on_tool, NOW + timedelta(minutes=1)))
        self.assertIsNone(self.call(msk.on_tool, agent_id="a1"))

    # --- запуск как хука ---

    def run_hook(self, mode, event, **env):
        home = self._tmp.name
        env = {**os.environ, "HOME": home, "USERPROFILE": home, **env}
        r = subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(event).encode(),
                           capture_output=True, env=env, timeout=30)
        return r.returncode, r.stdout.decode("utf-8")

    def test_process(self):
        event = {"session_id": "p1", "last_assistant_message": "без отметки"}
        code, out = self.run_hook("stop", event, CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")
        # без человека (claude -p, Agent SDK) — не трогает
        self.assertEqual(self.run_hook("stop", event, CLAUDE_CODE_ENTRYPOINT="sdk-cli"), (0, ""))
        # подкоманды install больше нет: тихий выход, settings.json не появляется
        self.assertEqual(self.run_hook("install", {}, CLAUDE_CODE_ENTRYPOINT="cli"), (0, ""))
        self.assertFalse((Path(self._tmp.name) / ".claude" / "settings.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
