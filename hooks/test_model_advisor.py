"""Проверка model-advisor.py без Claude Code: запуск как хука на выдуманной сессии во временном каталоге.

Запуск: python hooks/test_model_advisor.py
"""

import importlib.util
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
HOOK = HERE / "model-advisor.py"
STUB = HERE.parent / "terminal" / "hooks" / "model-advisor.py"
OPUS = "claude-opus-5-5"
DAY = 86400
POSIX = os.name != "nt"


class Session:
    """Выдуманная сессия: подменённые HOME, USERPROFILE и CLAUDE_CONFIG_DIR, файлы источников и вызовы хука."""

    def __init__(self, tmp, sid="s1"):
        self.tmp = Path(tmp)
        self.sid = sid
        self.claude = self.tmp / ".claude"
        self.status = self.claude / "session-status"
        self.status.mkdir(parents=True)
        self.transcript = self.tmp / f"{sid}.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.bin = self.tmp / "bin"
        self.env = {**os.environ, "HOME": str(self.tmp), "USERPROFILE": str(self.tmp),
                    "CLAUDE_CONFIG_DIR": str(self.claude)}
        for key in ("HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_ENV", "MODEL_ADVISOR_WAIT", "CLAUDE_EFFORT",
                    "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
                    "CLAUDE_CODE_DISABLE_1M_CONTEXT"):
            self.env.pop(key, None)
        self.now = time.time()

    # --- источники ---

    def mod(self, **data):
        (self.status / f"{self.sid}.state.json").write_text(
            json.dumps({"v": 1, "session_id": self.sid, **data}), encoding="utf-8")

    def full_mod(self, tokens=272_000, five=3, seven=4, at=None, model=OPUS, effort="high", reset5=3600, **extra):
        """Мод со всем: модель, effort, контекст с порогом 465k и оба лимита."""
        at = at or self.now
        self.mod(model={"id": model, "at": at}, effort={"level": effort, "at": at},
                 context={"tokens": tokens, "window": 1_000_000, "auto_compact_at": 465_000, "at": at},
                 rate_limits={"five_hour": {"used_percentage": five, "resets_at": int(self.now + reset5), "at": at},
                              "seven_day": {"used_percentage": seven, "resets_at": int(self.now + 3 * DAY),
                                            "at": at}},
                 **extra)

    def statusline(self, **data):
        (self.status / f"{self.sid}.json").write_text(json.dumps(data), encoding="utf-8")

    def week_budget(self, day_start, start_pct):
        (self.status / "week-budget.json").write_text(
            json.dumps({"day_start": day_start, "start_pct": start_pct}), encoding="utf-8")

    def fake_herdr(self, label="[3] Моя задача"):
        """herdr в PATH: пишет аргументы в файл и отвечает JSON с меткой вкладки."""
        self.bin.mkdir(exist_ok=True)
        exe = self.bin / "herdr"
        exe.write_text("#!/bin/sh\necho \"$@\" >> \"$HERDR_LOG\"\n"
                       "echo '{\"result\": {\"tab\": {\"label\": \"" + label + "\"}}}'\n", encoding="utf-8")
        exe.chmod(0o755)
        self.env["PATH"] = f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"
        self.env["HERDR_LOG"] = str(self.tmp / "herdr.log")

    def herdr_calls(self):
        log = self.tmp / "herdr.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    # --- вызовы ---

    def run(self, script, raw):
        r = subprocess.run([sys.executable, str(script)], input=raw, capture_output=True, env=self.env,
                           timeout=30)
        return r.returncode, r.stdout, r.stderr.decode("utf-8")

    def prompt(self, text="сделай задачу", script=HOOK, transcript=True, **fields):
        inp = {"session_id": self.sid, "cwd": str(self.tmp), "hook_event_name": "UserPromptSubmit",
               "prompt": text, **fields}
        if transcript:
            inp["transcript_path"] = str(self.transcript)
        code, out, err = self.run(script, json.dumps(inp).encode())
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else None
        return code, context, err


class AdvisorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def line(self, **kw):
        code, ctx, err = self.s.prompt(**kw)
        self.assertEqual((code, err), (0, ""))
        self.assertIsNotNone(ctx)
        return ctx

    def test_mod_only_without_herdr(self):
        self.s.full_mod()  # 272k из 465k — 58 %
        ctx = self.line()
        self.assertTrue(ctx.startswith(f"model-advisor: {OPUS}, effort high, контекст 272k — 58% до автосжатия "
                                       "(465k), ultracode выкл; 5ч 3% (сброс "), ctx)
        self.assertIn("7д 4% (сброс ", ctx)
        self.assertIn("сегодня ", ctx)
        self.assertTrue(ctx.endswith("knowledge/model-advice.md."), ctx)
        self.assertNotIn("wait:", ctx)
        self.assertNotIn("claude-autocontinue", ctx)
        self.assertNotIn("Вкладка", ctx)
        self.assertNotIn("tight", ctx)
        self.assertNotIn("лимиты — данные", ctx)

    @unittest.skipUnless(POSIX, "фальшивый herdr — скрипт sh")
    def test_herdr_without_advice_has_no_wait(self):
        """Повода нет — ни совета, ни wait:, только метка вкладки (поправка владельца 07.10.2026)."""
        self.s.full_mod()
        self.s.fake_herdr()
        self.s.env.update(HERDR_PANE_ID="p1", HERDR_TAB_ID="t1")
        ctx = self.line()
        self.assertNotIn("wait:", ctx)
        self.assertNotIn("Совет", ctx)
        self.assertTrue(ctx.endswith("knowledge/model-advice.md. Вкладка herdr: «Моя задача» — менять только "
                                     "при смене основной задачи (правила слоя terminal)."), ctx)
        self.assertEqual(self.s.herdr_calls(), ["tab get t1"])

    @unittest.skipUnless(POSIX, "фальшивый herdr — скрипт sh")
    def test_herdr_advice_adds_wait_once(self):
        self.s.full_mod(five=75)
        self.s.fake_herdr()
        self.s.env.update(HERDR_PANE_ID="p1", HERDR_TAB_ID="t1")
        ctx = self.line()
        self.assertIn("; limits tight. Правила", ctx)
        self.assertIn(" Совет (лимиты на исходе): Opus → Sonnet — предложить до работы. "
                      "wait: `~/.claude/bin/claude-autocontinue s1 120` (2 мин). Вкладка herdr: «Моя задача»", ctx)
        # Совет дан и проигнорирован (или принят): в той же ситуации больше ни совета, ни wait:.
        again = self.line()
        self.assertIn("limits tight", again)
        self.assertNotIn("Совет", again)
        self.assertNotIn("wait:", again)
        self.assertIn("Вкладка herdr", again)

    @unittest.skipUnless(POSIX, "фальшивый herdr — скрипт sh")
    def test_herdr_wait_env_and_no_tab(self):
        self.s.full_mod(five=80)
        self.s.fake_herdr()
        self.s.env.update(HERDR_PANE_ID="p1", MODEL_ADVISOR_WAIT="90")
        ctx = self.line()
        self.assertIn("claude-autocontinue s1 90` (90 с).", ctx)
        self.assertNotIn("Вкладка", ctx)
        self.assertEqual(self.s.herdr_calls(), [])

    @unittest.skipUnless(POSIX, "фальшивый herdr — скрипт sh")
    def test_tab_without_pane_is_not_herdr(self):
        self.s.full_mod(five=80)
        self.s.fake_herdr()
        self.s.env["HERDR_TAB_ID"] = "t1"
        ctx = self.line()
        self.assertIn("Совет", ctx)
        self.assertNotIn("wait:", ctx)
        self.assertNotIn("Вкладка", ctx)
        self.assertEqual(self.s.herdr_calls(), [])

    def test_herdr_missing_binary_keeps_wait(self):
        self.s.full_mod(five=80)
        self.s.env.update(HERDR_PANE_ID="p1", HERDR_TAB_ID="t1", HOME=str(self.s.tmp / "нет"),
                          PATH=str(self.s.tmp / "пусто"))
        ctx = self.line()
        self.assertIn("claude-autocontinue s1 120", ctx)
        self.assertNotIn("Вкладка", ctx)

    def test_advice_without_herdr_once_no_wait(self):
        self.s.full_mod(five=80)
        ctx = self.line()
        self.assertIn("Совет (лимиты на исходе): Opus → Sonnet — предложить до работы.", ctx)
        self.assertNotIn("wait:", ctx)
        self.assertNotIn("Совет", self.line())

    def test_new_window_gives_advice_again(self):
        self.s.full_mod(five=80)
        self.assertIn("Совет", self.line())
        self.s.full_mod(five=80)
        self.assertNotIn("Совет", self.line())
        self.s.full_mod(five=72, reset5=5 * 3600)  # новое пятичасовое окно — новая ситуация
        self.assertIn("Совет", self.line())

    def test_cheap_settings_no_advice(self):
        self.s.full_mod(five=90, model="claude-sonnet-5-5", effort="medium")
        ctx = self.line()
        self.assertIn("limits tight", ctx)
        self.assertNotIn("Совет", ctx)
        self.assertFalse((self.s.status / "s1.advice.json").exists())

    def test_unwritable_state_no_advice(self):
        """Не записалось «совет дан» — совета нет: иначе он шёл бы на каждом промпте."""
        (self.s.status / "s1.advice.json").mkdir()
        self.s.full_mod(five=90)
        self.assertNotIn("Совет", self.line())

    def test_broken_state_is_ignored(self):
        (self.s.status / "s1.advice.json").write_text("{битый", encoding="utf-8")
        self.s.full_mod(five=90)
        self.assertIn("Совет", self.line())

    def test_no_data_is_silent(self):
        self.assertEqual(self.s.prompt(), (0, None, ""))
        self.s.transcript.unlink()
        self.assertEqual(self.s.prompt(), (0, None, ""))

    def test_broken_input_is_silent(self):
        for raw in (b"", "{битый".encode(), b"[1, 2]", b"null", b'{"session_id": 5}', b'{"session_id": ""}'):
            code, out, err = self.s.run(HOOK, raw)
            self.assertEqual((code, out, err), (0, b"", ""), raw)

    def test_broken_mod_is_silent(self):
        (self.s.status / "s1.state.json").write_text("{битый", encoding="utf-8")
        self.assertEqual(self.s.prompt(), (0, None, ""))

    def test_subagent_is_silent(self):
        self.s.full_mod()
        self.assertEqual(self.s.prompt(agent_id="a1"), (0, None, ""))

    def test_only_limits_model_unknown(self):
        self.s.mod(rate_limits={"five_hour": {"used_percentage": 20, "resets_at": int(self.s.now + 3600),
                                              "at": self.s.now}})
        ctx = self.line()
        self.assertTrue(ctx.startswith("model-advisor: неизвестно, effort ?, ultracode выкл; 5ч 20%"), ctx)
        self.assertNotIn("контекст", ctx)

    def test_statusline_only(self):
        self.s.statusline(model=OPUS + "[1m]", name="Opus 5.5 (1M context)", effort="xhigh", ultracode=True,
                          window=1_000_000, used_pct=40)
        ctx = self.line(transcript=False)
        self.assertTrue(ctx.startswith("model-advisor: Opus 5.5 (1M context), effort xhigh, контекст 400k — 41% "
                                       "до автосжатия (967k), ultracode вкл. "), ctx)

    def test_ultracode_from_transcript(self):
        self.s.full_mod()
        self.s.transcript.write_text(json.dumps({"type": "attachment", "attachment": {
            "type": "ultra_effort_enter"}}) + "\n", encoding="utf-8")
        self.assertIn("ultracode вкл", self.line())
        self.assertIn("ultracode ?", self.line(transcript=False))

    def test_effort_unknown_is_question_mark(self):
        self.s.mod(model={"id": OPUS, "at": self.s.now})
        self.assertIn(f"{OPUS}, effort ?, ", self.line())

    def test_auto_compact_off_has_no_threshold(self):
        # Мод: автосжатие выключено — порога нет, формулу «− 33k» в строку не подставлять.
        self.s.mod(model={"id": OPUS, "at": self.s.now}, context={
            "tokens": 300_000, "window": 1_000_000, "auto_compact_at": None, "auto_compact_enabled": False,
            "at": self.s.now})
        ctx = self.line()
        self.assertIn("контекст 300k, автосжатие выкл, ultracode", ctx)
        self.assertNotIn("до автосжатия", ctx)

    def test_output_is_ascii_json(self):
        self.s.full_mod()
        code, out, _ = self.s.run(HOOK, json.dumps({"session_id": "s1", "prompt": "x"}).encode())
        self.assertEqual(code, 0)
        out.decode("ascii")
        self.assertIn("до автосжатия", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_skip_prompts(self):
        self.s.full_mod()
        for text in ("/session-extract-knowledge", "  /vasart:session-end сегодня всё", "/session-end",
                     "Контекст сжат, знания разложены. Продолжай", "Контекст сжат (claude-compact). Продолжи по инструкции",
                     "Продолжай: на совет по модели ответа не было"):
            self.assertEqual(self.s.prompt(text), (0, None, ""), text)
        self.assertIsNotNone(self.s.prompt("расскажи про /session-end")[1])

    def test_advised_marker_skips_one_prompt(self):
        self.s.full_mod()
        marker = self.s.status / "s1.advised"
        marker.write_text("", encoding="utf-8")
        self.assertEqual(self.s.prompt(), (0, None, ""))
        self.assertFalse(marker.exists())
        self.assertIsNotNone(self.s.prompt()[1])

    def test_tight_by_five_hour(self):
        self.s.full_mod(five=69)
        ctx = self.line()
        self.assertNotIn("tight", ctx)
        self.assertNotIn("Совет", ctx)
        self.s.full_mod(five=70)
        self.assertIn("; limits tight. Правила — «Модель и effort» корневых правил и knowledge/model-advice.md. "
                      "Совет (лимиты на исходе): Opus → Sonnet", self.line())

    def test_tight_by_day_norm(self):
        s = self.s
        reset = int(s.now + 3 * DAY)
        ds = (reset - 7 * DAY) + int((s.now - (reset - 7 * DAY)) // DAY) * DAY
        s.full_mod(seven=51)
        s.week_budget(ds, 50)  # потрачен 1 % при норме не меньше 7 %
        ctx = self.line()
        self.assertRegex(ctx, r"7д 51% \(сброс \d\d\.\d\d \d\d:\d\d, сегодня 1/\d+% нормы\)")
        self.assertNotIn("tight", ctx)
        s.full_mod(seven=100)
        s.week_budget(ds, 50)  # потрачено 50 % при норме не больше 50 %
        ctx = self.line()
        self.assertIn("limits tight", ctx)
        self.assertIn("Совет", ctx)
        self.assertIn(f"day:{ds}", (s.status / "s1.advice.json").read_text(encoding="utf-8"))

    def test_expired_limit_is_not_shown(self):
        s = self.s
        s.mod(model={"id": OPUS, "at": s.now},
              rate_limits={"five_hour": {"used_percentage": 95, "resets_at": int(s.now - 60), "at": s.now - 600},
                           "seven_day": {"used_percentage": 30, "resets_at": int(s.now + DAY), "at": s.now}})
        ctx = self.line()
        self.assertNotIn("5ч", ctx)
        self.assertNotIn("tight", ctx)
        self.assertIn("7д 30%", ctx)

    def test_old_reading_is_marked(self):
        self.s.full_mod(at=self.s.now - 20 * 60)
        self.assertIn("), лимиты — данные 20 мин назад", self.line())
        self.s.full_mod(at=self.s.now - 14 * 60)
        self.assertNotIn("лимиты — данные", self.line())

    def test_limits_from_other_session(self):
        """Лимит общий для аккаунта: файл другой сессии даёт показание, когда у своей его нет."""
        self.s.mod(model={"id": OPUS, "at": self.s.now})
        (self.s.status / "other.state.json").write_text(json.dumps({"rate_limits": {"five_hour": {
            "used_percentage": 55, "resets_at": int(self.s.now + 600), "at": self.s.now - 30}}}), encoding="utf-8")
        self.assertIn("5ч 55%", self.line())


def load_hook():
    spec = importlib.util.spec_from_file_location("model_advisor", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DecideTest(unittest.TestCase):
    """Таблица решения без запуска хука: что сменить и когда совет положен."""

    @classmethod
    def setUpClass(cls):
        cls.m = load_hook()

    def test_cheaper_table(self):
        cases = [
            (("claude-fable-5-1", "high", False), ["Fable → Opus"]),
            (("claude-fable-5-1", "max", True), ["ultracode выкл", "Fable → Opus"]),
            ((OPUS, "xhigh", False), ["effort xhigh → high"]),
            ((OPUS, "max", None), ["effort max → high"]),
            ((OPUS, "high", False), ["Opus → Sonnet"]),
            ((OPUS + "[1m]", "low", False), ["Opus → Sonnet"]),
            (("Opus 5.5 (1M context)", None, False), ["Opus → Sonnet"]),
            (("claude-sonnet-5-5", "high", False), []),
            (("claude-sonnet-5-5", "max", False), ["effort max → high"]),
            (("claude-sonnet-5-5", "medium", True), ["ultracode выкл"]),
            (("claude-haiku-4-5", None, False), []),
            ((None, None, None), []),
        ]
        for (model, effort, ultra), want in cases:
            st = {"model": model, "effort": effort, "ultracode": ultra}
            self.assertEqual(self.m.cheaper(st), want, (model, effort, ultra))

    def test_decide_needs_trigger_and_fresh_key(self):
        now = time.time()
        h5 = lambda used: {"used": used, "resets_at": now + 600, "age": 0}
        base = {"model": OPUS, "effort": "high", "ultracode": False}
        key = f"5h:{round((now + 600) / 60)}"
        self.assertEqual(self.m.decide({**base, "limits": {"five_hour": h5(69)}}, []), (None, []))
        self.assertEqual(self.m.decide({**base, "limits": {"five_hour": h5(70)}}, []), ("Opus → Sonnet", [key]))
        self.assertEqual(self.m.decide({**base, "limits": {"five_hour": h5(99)}}, [key]), (None, []))
        self.assertEqual(self.m.decide({**base, "model": "claude-sonnet-5-5", "limits": {"five_hour": h5(99)}}, []),
                         (None, []))


@unittest.skipUnless(POSIX, "заглушка перенаправляет через os.execv")
class StubTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Session(self._tmp.name)
        self.s.full_mod()

    def tearDown(self):
        self._tmp.cleanup()

    def test_stub_forwards_stdin_to_new_hook(self):
        direct = self.s.prompt()
        via_stub = self.s.prompt(script=STUB)
        self.assertIsNotNone(direct[1])
        self.assertEqual(via_stub, direct)

    def test_stub_without_new_hook_exits_silently(self):
        kit = Path(self._tmp.name) / "kit"
        (kit / "terminal" / "hooks").mkdir(parents=True)
        copy = kit / "terminal" / "hooks" / "model-advisor.py"
        shutil.copy(STUB, copy)
        self.assertEqual(self.s.prompt(script=copy), (0, None, ""))


if __name__ == "__main__":
    unittest.main()
