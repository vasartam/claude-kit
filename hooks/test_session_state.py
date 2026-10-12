"""Проверка session_state.py без Claude Code: источники, свежесть, порог, лимиты, дневная норма.

Запуск: python hooks/test_session_state.py
"""

import contextlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MODULE = HERE / "session_state.py"
STATUSLINE = HERE.parent / "terminal" / "bin" / "claude-statusline"
_spec = importlib.util.spec_from_file_location("session_state", MODULE)
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)

OPUS = "claude-opus-5-5"
HAIKU = "claude-haiku-4-5-20251001"
DAY = 86400
# Понедельник 28.09.2026 09:00 по местному времени: начало недели лимита, сброс — через 7 дней.
MON = datetime(2026, 9, 28, 9, 0).timestamp()
WED, SAT = MON + 2 * DAY, MON + 5 * DAY
R7 = MON + 7 * DAY


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Case(unittest.TestCase):
    """Подменённые HOME, USERPROFILE и CLAUDE_CONFIG_DIR (= <HOME>/.claude), сессия s1."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.claude = self.home / ".claude"
        self.status = self.claude / "session-status"
        self.status.mkdir(parents=True)
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                                 "CLAUDE_CONFIG_DIR": str(self.claude)})
        self._env.start()
        for key in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
                    "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_EFFORT"):
            os.environ.pop(key, None)
        self.sid = "s1"
        self.transcript = self.home / "s1.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        self.now = time.time()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    # --- запись источников ---

    def append(self, entry, compact=False):
        sep = (",", ":") if compact else None
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=sep) + "\n")

    def answer(self, tokens, at, model=OPUS, effort="high", **extra):
        """Ответ модели основной ветки: в контексте `tokens` (output в счёт не идёт)."""
        entry = {"type": "assistant", "isSidechain": False, "timestamp": iso(at), **extra,
                 "message": {"model": model, "usage": {
                     "input_tokens": 2, "cache_creation_input_tokens": 1000,
                     "cache_read_input_tokens": tokens - 1002, "output_tokens": 700}}}
        if effort is not None:
            entry["effort"] = effort
        self.append(entry)

    def boundary(self, post, at):
        self.append({"type": "system", "subtype": "compact_boundary", "timestamp": iso(at),
                     "compactMetadata": {"trigger": "auto", "preTokens": 967_000, "postTokens": post}})

    def model_attachment(self, model, at):
        self.append({"type": "attachment", "timestamp": iso(at), "attachment": {
            "type": "model", "identity": {"modelId": model},
            "text": f"You are powered by the model. The exact model ID is {model}."}}, compact=True)

    def mod(self, sid=None, **data):
        path = self.status / f"{sid or self.sid}.state.json"
        path.write_text(json.dumps({"v": 1, "session_id": sid or self.sid, **data}), encoding="utf-8")
        return path

    def statusline(self, mtime, **data):
        path = self.status / f"{self.sid}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def inp(self, event="PostToolUse", **extra):
        return {"session_id": self.sid, "transcript_path": str(self.transcript), "cwd": str(self.home),
                "hook_event_name": event, **extra}

    def state(self, event="PostToolUse", fields=ss.FIELDS_ALL, **extra):
        return ss.session_state(self.inp(event, **extra), fields=fields, now=self.now)


class FreshnessTest(Case):
    def test_mod_newer_than_transcript(self):
        self.answer(300_000, self.now - 60)
        self.mod(context={"tokens": 320_000, "window": 1_000_000, "at": self.now - 10})
        st = self.state()
        self.assertEqual((st["tokens"], st["src"]["tokens"], st["after_compact"]), (320_000, "mod", False))

    def test_transcript_newer_than_mod(self):
        self.mod(context={"tokens": 300_000, "at": self.now - 60})
        self.answer(350_000, self.now - 5)
        st = self.state()
        self.assertEqual((st["tokens"], st["src"]["tokens"]), (350_000, "transcript"))

    def test_tie_goes_to_mod(self):
        self.mod(context={"tokens": 300_000, "at": self.now - 10})
        self.answer(310_000, self.now - 9)
        st = self.state()
        self.assertEqual((st["tokens"], st["src"]["tokens"]), (300_000, "mod"))

    def test_boundary_sets_after_compact_until_mod_measures(self):
        self.mod(context={"tokens": 880_000, "at": self.now - 200})
        self.answer(900_000, self.now - 100)
        self.boundary(30_000, self.now - 50)
        st = self.state()
        self.assertEqual((st["tokens"], st["after_compact"], st["src"]["tokens"]), (30_000, True, "transcript"))
        self.mod(context={"tokens": 35_000, "at": self.now})
        st = self.state()
        self.assertEqual((st["tokens"], st["after_compact"], st["src"]["tokens"]), (35_000, False, "mod"))

    def test_boundary_without_post_tokens_beats_older_mod(self):
        # В старых транскриптах у границы бывает только preTokens: замер мода до сжатия уже неверен.
        self.mod(context={"tokens": 880_000, "at": self.now - 200})
        self.answer(900_000, self.now - 100)
        self.append({"type": "system", "subtype": "compact_boundary", "timestamp": iso(self.now - 50),
                     "compactMetadata": {"trigger": "auto", "preTokens": 900_000}})
        st = self.state()
        self.assertEqual((st["tokens"], st["after_compact"], st["fill_pct"]), (None, True, None))
        self.statusline(self.now, model=OPUS, window=1_000_000, used_pct=0)
        st = self.state()
        self.assertEqual((st["tokens"], st["after_compact"], st["src"]["tokens"]), (0, True, "statusline~"))

    def test_readings_before_compact_mark_are_stale(self):
        # Первый промпт после сжатия: граница ещё не в транскрипте (Claude Code дописывает его с
        # задержкой), а отметка context-guard уже есть — последний ответ до сжатия не годится.
        self.mod(context={"tokens": 318_000, "at": self.now - 90})
        self.answer(319_000, self.now - 80)
        self.statusline(self.now, model=OPUS, window=1_000_000, used_pct=31)
        ss.note_compact(self.sid, self.now - 60)
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["tokens"], st["after_compact"], st["src"]["tokens"]), (None, True, None))
        self.boundary(28_000, self.now - 1)  # граница дописана — её показание новее отметки
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["tokens"], st["after_compact"], st["src"]["tokens"]), (28_000, True, "transcript"))

    def test_statusline_mtime_now_does_not_win_tokens(self):
        self.statusline(self.now, model=OPUS + "[1m]", name="Opus 5.5 (1M context)", effort="xhigh",
                        ultracode=False, window=1_000_000, used_pct=40)
        self.answer(300_000, self.now - 30)
        self.boundary(25_000, self.now - 20)
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["tokens"], st["src"]["tokens"], st["after_compact"]), (25_000, "transcript", True))
        self.assertEqual((st["model"], st["src"]["model"]), (OPUS + "[1m]", "statusline"))
        self.assertEqual(st["model_name"], "Opus 5.5 (1M context)")
        self.assertEqual((st["effort"], st["src"]["effort"]), ("xhigh", "statusline"))
        self.assertEqual((st["window"], st["src"]["window"]), (1_000_000, "statusline"))


class RobustnessTest(Case):
    def test_broken_empty_or_missing_mod(self):
        path = self.status / "s1.state.json"
        for text in ("", "{битый", "[1, 2]", '{"context": "x", "model": 5, "effort": [], "rate_limits": 1}'):
            path.write_text(text, encoding="utf-8")
            st = self.state()
            self.assertIsNone(st["model"], text)
            self.assertIsNone(st["tokens"], text)
            self.assertEqual(st["limits"], {}, text)
        path.unlink()
        self.assertIsNone(ss.read_mod(self.sid))

    def test_nothing_at_all(self):
        self.transcript.unlink()
        st = self.state()
        for key in ("model", "model_name", "effort", "ultracode", "tokens", "fill_pct", "day"):
            self.assertIsNone(st[key], key)
        self.assertEqual((st["window"], st["auto_compact_at"], st["limits"]), (1_000_000, 967_000, {}))
        self.assertEqual(st["src"]["window"], "default")
        st = ss.session_state(None)
        self.assertIsNone(st["model"])
        st = ss.session_state({"session_id": 5, "transcript_path": ["x"], "cwd": 7, "effort": {}})
        self.assertIsNone(st["tokens"])

    def test_statusline_tokens_only_as_fallback(self):
        self.statusline(self.now - 5, model=HAIKU, name="Haiku 4.5", effort=None, window=200_000, used_pct=30)
        self.transcript.unlink()
        st = self.state()
        self.assertEqual((st["tokens"], st["src"]["tokens"]), (60_000, "statusline~"))
        self.assertEqual((st["window"], st["auto_compact_at"], st["fill_pct"]), (200_000, 167_000, 35))
        self.assertIsNone(st["effort"])

    def test_model_name_only_when_model_matches(self):
        self.statusline(self.now - 100, model=OPUS, name="Opus 5.5", effort="high", window=1_000_000)
        self.answer(100_000, self.now - 60)
        self.model_attachment(HAIKU, self.now - 5)
        st = self.state()
        self.assertEqual((st["model"], st["src"]["model"]), (HAIKU, "transcript"))
        self.assertEqual(st["model_name"], HAIKU)
        self.statusline(self.now, model=OPUS, name="Opus 5.5", effort="high", window=1_000_000)
        st = self.state()
        self.assertEqual((st["model"], st["model_name"], st["src"]["model_name"]), (OPUS, "Opus 5.5", "statusline"))

    def test_mod_model_name_ignores_1m_suffix(self):
        self.mod(model={"id": OPUS, "at": self.now})
        self.statusline(self.now - 60, model=OPUS + "[1m]", name="Opus 5.5 (1M context)")
        st = self.state()
        self.assertEqual((st["model"], st["model_name"]), (OPUS, "Opus 5.5 (1M context)"))


class ModelEffortTest(Case):
    def test_synthetic_skipped(self):
        self.answer(300_000, self.now - 50, effort="xhigh")
        self.append({"type": "assistant", "timestamp": iso(self.now - 10), "effort": None, "message": {
            "model": "<synthetic>", "usage": {"input_tokens": 0, "output_tokens": 0}}})
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["model"], st["tokens"], st["effort"]), (OPUS, 300_000, "xhigh"))

    def test_sidechain_skipped(self):
        self.answer(300_000, self.now - 50)
        self.answer(50_000, self.now - 10, model=HAIKU, isSidechain=True)
        st = self.state()
        self.assertEqual((st["model"], st["tokens"]), (OPUS, 300_000))

    def test_model_attachment_after_switch_beats_old_answer(self):
        self.answer(150_000, self.now - 50)
        self.model_attachment(HAIKU, self.now - 20)
        st = self.state()
        self.assertEqual((st["model"], st["src"]["model"]), (HAIKU, "transcript"))
        self.assertEqual((st["window"], st["auto_compact_at"]), (200_000, 167_000))

    def test_effort_from_hook_input(self):
        self.answer(100_000, self.now - 5, effort="high")
        st = self.state(effort={"level": "max"})
        self.assertEqual((st["effort"], st["src"]["effort"]), ("max", "hook"))

    def test_effort_env_not_on_prompt(self):
        os.environ["CLAUDE_EFFORT"] = "low"
        self.answer(100_000, self.now - 5, effort="high")
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["effort"], st["src"]["effort"]), ("high", "transcript"))
        st = self.state("PostToolUse")
        self.assertEqual((st["effort"], st["src"]["effort"]), ("low", "env"))

    def test_effort_from_transcript_without_mod(self):
        self.answer(100_000, self.now - 5, effort="xhigh")
        st = ss.read_model(self.inp("UserPromptSubmit"))
        self.assertEqual((st["effort"], st["src"]["effort"]), ("xhigh", "transcript"))
        self.assertEqual(set(st), {"model", "model_name", "effort", "src"})

    def test_effort_from_mod_when_newer(self):
        self.answer(100_000, self.now - 60, effort="high")
        self.mod(effort={"level": "medium", "at": self.now - 1}, model={"id": OPUS, "at": self.now - 1})
        st = self.state("UserPromptSubmit")
        self.assertEqual((st["effort"], st["src"]["effort"], st["src"]["model"]), ("medium", "mod", "mod"))


class ThresholdTest(Case):
    def test_threshold_priority(self):
        self.answer(400_000, self.now - 5)
        mod = self.mod(context={"auto_compact_at": 400_000, "at": self.now - 100})
        st = self.state()
        self.assertEqual((st["auto_compact_at"], st["src"]["auto_compact_at"], st["fill_pct"]), (400_000, "mod", 100))
        self.assertTrue(st["auto_compact_enabled"])
        mod.unlink()
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "600k"
        project = self.home / "proj" / ".claude"
        project.mkdir(parents=True)
        (project / "settings.local.json").write_text('{"autoCompactWindow": 500000}', encoding="utf-8")
        (project / "settings.json").write_text('{"autoCompactWindow": "700k"}', encoding="utf-8")
        (self.claude / "settings.json").write_text('{"autoCompactWindow": 800000}', encoding="utf-8")
        cwd = str(self.home / "proj")
        expect = [(567_000, "env"), (467_000, "settings"), (667_000, "settings"), (767_000, "settings"),
                  (967_000, "default")]
        removals = [lambda: os.environ.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW"),
                    (project / "settings.local.json").unlink, (project / "settings.json").unlink,
                    (self.claude / "settings.json").unlink]
        for i, (value, src) in enumerate(expect):
            st = ss.read_context(self.inp(), cwd=cwd)
            self.assertEqual((st["auto_compact_at"], st["src"]["auto_compact_at"]), (value, src), i)
            self.assertIsNone(st["auto_compact_enabled"])
            if i < len(removals):
                removals[i]()

    def test_200k_model_thresholds(self):
        self.assertEqual(ss.autocompact_at(HAIKU, None), 167_000)
        self.assertEqual(ss.window_for(HAIKU), 200_000)
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "500000"
        self.assertEqual(ss.autocompact_at(OPUS, None), 467_000)
        os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "2m"  # явный больше окна — по окну
        self.assertEqual(ss.autocompact_at(HAIKU, None), 167_000)
        del os.environ["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        self.assertEqual(ss.autocompact_at(OPUS, None), 967_000)
        self.assertEqual(ss.autocompact_at(None, None), 967_000)
        self.assertEqual(ss.AUTOCOMPACT_RESERVE, 33_000)

    def test_window_env_and_mod(self):
        os.environ["CLAUDE_CODE_DISABLE_1M_CONTEXT"] = "1"
        self.answer(100_000, self.now - 5)
        st = self.state()
        self.assertEqual((st["window"], st["src"]["window"], st["auto_compact_at"]), (200_000, "env", 167_000))
        self.mod(context={"window": 1_000_000, "auto_compact_at": None, "auto_compact_enabled": False,
                          "at": self.now})
        st = self.state()
        self.assertEqual((st["window"], st["src"]["window"]), (1_000_000, "mod"))
        self.assertEqual((st["auto_compact_at"], st["auto_compact_enabled"], st["fill_pct"]), (None, False, None))
        self.assertEqual(st["src"]["auto_compact_enabled"], "mod")


class LimitsTest(Case):
    def write_rate_limits(self, ts, **windows):
        (self.status / "rate-limits.json").write_text(json.dumps({**windows, "ts": ts}), encoding="utf-8")

    def test_merge_mod_files_and_statusline(self):
        r5, r7 = self.now + 3600, self.now + 3 * DAY
        self.mod("a", ts=self.now - 300, rate_limits={
            "five_hour": {"used_percentage": 10, "resets_at": r5, "at": self.now - 300},
            "seven_day": {"used_percentage": 40, "resets_at": r7, "at": self.now - 300}})
        self.mod("b", rate_limits={"five_hour": {"used_percentage": 25, "resets_at": r5, "at": self.now - 60}})
        self.write_rate_limits(self.now - 120, five_hour={"used_percentage": 20, "resets_at": r5},
                               seven_day={"used_percentage": 45, "resets_at": r7})
        lim = ss.read_limits(self.now)
        self.assertEqual((lim["five_hour"]["used"], lim["five_hour"]["src"]), (25, "mod"))
        self.assertAlmostEqual(lim["five_hour"]["age"], 60, delta=1)
        self.assertEqual((lim["seven_day"]["used"], lim["seven_day"]["src"]), (45, "statusline"))
        self.assertAlmostEqual(lim["seven_day"]["age"], 120, delta=1)
        self.assertEqual(lim["seven_day"]["resets_at"], r7)

    def test_passed_reset_dropped_and_old_files_ignored(self):
        self.write_rate_limits(self.now - 4000, five_hour={"used_percentage": 90, "resets_at": self.now - 10},
                               seven_day={"used_percentage": 50, "resets_at": self.now + DAY})
        old = self.mod("old", rate_limits={"seven_day": {"used_percentage": 99, "resets_at": self.now + DAY,
                                                         "at": self.now - 9 * DAY}})
        os.utime(old, (self.now - 9 * DAY, self.now - 9 * DAY))
        lim = ss.read_limits(self.now)
        self.assertNotIn("five_hour", lim)
        self.assertEqual(lim["seven_day"]["used"], 50)
        self.assertEqual(ss.session_state(None, fields=("limits",), now=self.now)["limits"], lim)

    def test_old_reading_valid_until_reset(self):
        self.write_rate_limits(self.now - 3600, five_hour={"used_percentage": 92, "resets_at": self.now + 600})
        lim = ss.read_limits(self.now)
        self.assertEqual(lim["five_hour"]["used"], 92)
        self.assertAlmostEqual(lim["five_hour"]["age"], 3600, delta=1)

    def test_reset_rounded_to_minute(self):
        r7 = 1_791_352_800  # ровный час, как у настоящих сбросов
        self.mod("a", rate_limits={"seven_day": {"used_percentage": 50, "resets_at": r7 + 0.4, "at": self.now}})
        self.write_rate_limits(self.now - 500, seven_day={"used_percentage": 52, "resets_at": r7})
        lim = ss.read_limits(r7 - DAY)
        self.assertEqual((lim["seven_day"]["used"], lim["seven_day"]["src"]), (52, "statusline"))

    def test_nothing(self):
        shutil.rmtree(self.status)
        self.assertEqual(ss.read_limits(), {})


class DayBudgetTest(Case):
    def budget(self):
        path = self.status / "week-budget.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def set_budget(self, day_start, start_pct):
        (self.status / "week-budget.json").write_text(
            json.dumps({"day_start": day_start, "start_pct": start_pct}), encoding="utf-8")

    def test_first_day_of_week(self):
        day = ss.day_budget({"used": 4, "resets_at": R7, "at": MON + 3600}, MON + 3600)
        self.assertEqual((day["start"], day["spent"], day["norm"], day["day_start"]), (0, 4, 20, MON))
        self.assertEqual(self.budget(), {"day_start": MON, "start_pct": 0})

    def test_weekday_from_file(self):
        self.set_budget(WED, 30)
        day = ss.day_budget({"used": 35, "resets_at": R7, "at": WED + 3600}, WED + 3600)
        self.assertEqual((day["start"], day["spent"]), (30, 5))
        self.assertAlmostEqual(day["norm"], 70 / 3)
        self.assertEqual(self.budget(), {"day_start": WED, "start_pct": 30})

    def test_new_day_writes_file(self):
        self.set_budget(WED - DAY, 20)
        day = ss.day_budget({"used": 33, "resets_at": R7, "at": WED + 1800}, WED + 3600)
        self.assertEqual((day["start"], day["spent"], day["day_start"]), (33, 0, WED))
        self.assertAlmostEqual(day["norm"], 67 / 3)
        self.assertEqual(self.budget(), {"day_start": WED, "start_pct": 33})

    def test_weekend_is_one_day(self):
        day = ss.day_budget({"used": 70, "resets_at": R7, "at": SAT + 60}, SAT + 3600)
        self.assertEqual((day["start"], day["norm"]), (70, 30))

    def test_yesterdays_reading_not_written(self):
        self.set_budget(WED - DAY, 20)
        day = ss.day_budget({"used": 30, "resets_at": R7, "at": WED - 600}, WED + 600)
        self.assertEqual((day["start"], day["spent"], day["day_start"]), (30, 0, WED))
        self.assertEqual(self.budget(), {"day_start": WED - DAY, "start_pct": 20})

    def test_no_data(self):
        self.assertIsNone(ss.day_budget(None))
        self.assertIsNone(ss.day_budget({"used": 5}))
        self.assertIsNone(self.state()["day"])

    def test_session_state_day_from_limits(self):
        (self.status / "rate-limits.json").write_text(json.dumps(
            {"seven_day": {"used_percentage": 35, "resets_at": R7}, "ts": WED + 100}), encoding="utf-8")
        self.set_budget(WED, 30)
        st = ss.session_state(None, fields=("day",), now=WED + 3600)
        self.assertEqual(set(st), {"day", "src"})
        self.assertEqual(st["day"]["spent"], 5)

    @unittest.skipUnless(os.name != "nt" and shutil.which("bash") and shutil.which("jq"), "нужны bash и jq")
    def test_matches_statusline(self):
        now = time.time()
        reset = int(now + 3.5 * DAY)  # середина суток лимита: граница дня далеко
        ws = reset - 7 * DAY
        ds = ws + int((now - ws) // DAY) * DAY
        inp = {"session_id": "s1", "model": {"id": OPUS, "display_name": "Opus 5.5"},
               "rate_limits": {"seven_day": {"used_percentage": 40, "resets_at": reset}}}

        def statusline():
            env = {**os.environ, "HOME": str(self.home)}
            r = subprocess.run(["bash", str(STATUSLINE)], input=json.dumps(inp).encode(), env=env,
                               capture_output=True, timeout=30)
            m = re.search(r"сегодня \x1b\[[0-9;]*m(\d+)/(\d+)%", r.stdout.decode("utf-8"))
            self.assertIsNotNone(m, r.stdout.decode("utf-8") + r.stderr.decode("utf-8"))
            return int(m.group(1)), int(m.group(2))

        d7 = {"used": 40, "resets_at": reset, "at": now}
        # Начало дня уже записано: обе стороны берут его из файла.
        self.set_budget(ds, 31)
        shown = statusline()
        day = ss.day_budget(d7, now)
        self.assertEqual(shown, (math.floor(day["spent"]), math.floor(day["norm"])))
        self.assertEqual(day["start"], 31)
        # Файла нет: обе стороны записывают одно и то же.
        (self.status / "week-budget.json").unlink()
        shown = statusline()
        written = self.budget()
        (self.status / "week-budget.json").unlink()
        day = ss.day_budget(d7, now)
        self.assertEqual(self.budget(), written)
        self.assertEqual(shown, (math.floor(day["spent"]), math.floor(day["norm"])))


class LazyAndSpeedTest(Case):
    def test_fields_limits_does_not_touch_transcript(self):
        self.transcript.unlink()
        self.transcript.mkdir()  # каталог вместо файла
        with mock.patch.object(ss, "entries_backwards", wraps=ss.entries_backwards) as eb, \
                mock.patch.object(ss, "_ultra_scan", wraps=ss._ultra_scan) as us:
            st = ss.session_state(self.inp(), fields=("limits",))
            self.assertEqual(set(st), {"limits", "src"})
            self.assertEqual((eb.call_count, us.call_count), (0, 0))
            st = ss.session_state(self.inp())  # и полный набор на каталоге — без ошибки
            self.assertIsNone(st["tokens"])
            self.assertIsNone(st["ultracode"])

    def test_big_transcript_read_fast(self):
        self.answer(200_000, self.now - 3600)
        filler = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "x" * 100_000}]}}).encode() + b"\n"
        with self.transcript.open("ab") as f:
            f.write(filler * 500)  # ~50 МБ
        self.answer(420_000, self.now - 5)
        self.assertGreater(self.transcript.stat().st_size, 50_000_000)
        t0 = time.perf_counter()
        ss.read_limits()
        st = ss.read_context(self.inp())
        elapsed = time.perf_counter() - t0
        self.assertEqual(st["tokens"], 420_000)
        self.assertLess(elapsed, 0.5)

    def test_ultracode_reads_only_growth(self):
        enter = {"type": "attachment", "attachment": {"type": "ultra_effort_enter"}}
        self.append(enter, compact=True)
        self.answer(100_000, self.now - 5)
        self.assertTrue(ss.read_ultracode(self.inp())["ultracode"])
        cache = self.status / "s1.ultra.json"
        first = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(first["offset"], self.transcript.stat().st_size)
        # Прочитанное начало больше не перечитывается: стираем в нём событие, той же длиной.
        data = self.transcript.read_bytes().replace(b"ultra_effort_enter", b"ultra_effort_xxxxx")
        self.transcript.write_bytes(data)
        self.answer(110_000, self.now - 1)
        self.assertTrue(ss.read_ultracode(self.inp())["ultracode"])
        second = json.loads(cache.read_text(encoding="utf-8"))
        self.assertGreater(second["offset"], first["offset"])
        self.assertEqual(second["offset"], self.transcript.stat().st_size)
        # Файл стал короче offset — полный проход: событий больше нет.
        self.transcript.write_text("", encoding="utf-8")
        self.answer(5_000, self.now)
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], False)

    def test_ultracode_partial_line_read_later(self):
        self.answer(100_000, self.now - 5)
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write('{"type":"attachment","attachment":{"type":"ultra_eff')  # строка ещё пишется
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], False)
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write('ort_enter"}}\n')
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], True)


class UltracodeTest(Case):
    def command(self, text):
        self.append({"type": "user", "message": {"role": "user", "content": f"<local-command-stdout>{text}"}},
                    compact=True)

    def test_on_off(self):
        self.command("Ultracode on: Opus 5.5, max effort")
        st = ss.read_ultracode(self.inp())
        self.assertEqual((st["ultracode"], st["src"]["ultracode"]), (True, "transcript"))
        self.append({"type": "attachment", "attachment": {"type": "ultra_effort_exit"}}, compact=True)
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], False)
        self.command("Ultracode on")
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], True)
        self.command("Ultracode off.")
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], False)

    def test_no_events(self):
        self.answer(100_000, self.now)
        self.assertIs(ss.read_ultracode(self.inp())["ultracode"], False)

    def test_no_file(self):
        self.transcript.unlink()
        self.assertIsNone(ss.read_ultracode(self.inp())["ultracode"])
        self.statusline(self.now, model=OPUS, ultracode=True)
        st = ss.read_ultracode(self.inp())
        self.assertEqual((st["ultracode"], st["src"]["ultracode"]), (True, "statusline"))


class BricksTest(Case):
    def test_parse_tokens(self):
        cases = {"900000": 900_000, "900k": 900_000, "900K": 900_000, "0.9m": 900_000, "1.2M": 1_200_000,
                 "650к": 650_000, "0,65М": 650_000, " 650 k ": 650_000, "650": 650_000, "100": 100_000,
                 "5000": 5_000, 900_000: 900_000, 500_000.0: 500_000, "0.57m": 570_000}
        for raw, value in cases.items():
            self.assertEqual(ss.parse_tokens(raw), value, raw)
        for raw in ("auto", "", None, "abc", "12q", "-5k", 0, -1, True, [], "0"):
            self.assertIsNone(ss.parse_tokens(raw), raw)

    def test_write_json_replace_fails_quietly(self):
        target = self.status / "x.json"
        with mock.patch.object(ss.os, "replace", side_effect=PermissionError("занят")):
            self.assertFalse(ss.write_json(target, {"a": 1}))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.status.glob("x.json.tmp.*")), [])
        self.assertTrue(ss.write_json(self.status / "new" / "y.json", {"текст": "да"}))
        self.assertEqual(json.loads((self.status / "new" / "y.json").read_text(encoding="utf-8")), {"текст": "да"})

    def test_emit_context_ascii(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ss.emit_context("UserPromptSubmit", "контекст 300k — проверка")
        text = out.getvalue()
        self.assertTrue(text.isascii(), text)
        self.assertEqual(json.loads(text)["hookSpecificOutput"]["additionalContext"], "контекст 300k — проверка")

    def test_paths_follow_home_and_userprofile(self):
        del os.environ["CLAUDE_CONFIG_DIR"]
        self.assertEqual(ss.claude_dir(), self.home / ".claude")
        self.assertEqual(ss.status_dir(), self.status)
        self.assertEqual(ss.safe_sid("a/b c-1_2"), "a_b_c-1_2")
        self.assertEqual(ss.safe_sid(None), "unknown")

    def test_cleanup_status(self):
        old, fresh = self.now - 31 * DAY, self.now - DAY
        names = ["a.state.json", "a.ultra.json", "a.ctx-mark", "a.json.tmp.1", "a.json", "b.state.json"]
        for name in names:
            (self.status / name).write_text("{}", encoding="utf-8")
            t = fresh if name.startswith("b") else old
            os.utime(self.status / name, (t, t))
        ss.cleanup_status()
        self.assertEqual(sorted(p.name for p in self.status.iterdir()), ["a.json", "b.state.json"])

    def test_entries_backwards_long_lines_and_limit(self):
        self.append({"type": "user", "n": 1, "pad": "y" * (3 << 20)})
        self.append({"type": "user", "n": 2})
        self.assertEqual([e["n"] for e in ss.entries_backwards(self.transcript)], [2, 1])
        self.assertEqual([e["n"] for e in ss.entries_backwards(self.transcript, max_bytes=1 << 20)], [2])
        self.assertEqual(list(ss.entries_backwards(self.home)), [])
        self.assertEqual(list(ss.entries_backwards(None)), [])


class ProcessTest(unittest.TestCase):
    """Ввод-вывод хука в отдельном процессе: как его увидят хуки."""

    def run_py(self, code, stdin=b"", env=None):
        prog = f"import sys; sys.path.insert(0, {str(HERE)!r}); import session_state as ss\n{code}"
        # PYTHONIOENCODING: на Windows print в канал иначе идёт кодовой страницей и бьёт кириллицу.
        return subprocess.run([sys.executable, "-c", prog], input=stdin, capture_output=True, timeout=30,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})

    def test_read_input(self):
        code = "print(repr(ss.read_input()))"
        self.assertEqual(self.run_py(code, b"").stdout.strip(), b"None")
        self.assertEqual(self.run_py(code, b"{not json").stdout.strip(), b"None")
        self.assertEqual(self.run_py(code, b"[1, 2]").stdout.strip(), b"None")
        self.assertEqual(self.run_py(code, b"\xff\xfe").stdout.strip(), b"None")
        r = self.run_py(code, json.dumps({"prompt": "привет"}, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(r.stdout.decode("utf-8").strip(), "{'prompt': 'привет'}")

    def test_dump_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "dump.jsonl"
            r = self.run_py("print(ss.read_input('X_DUMP') is None)", b'{"a": 1}', {"X_DUMP": str(dump)})
            self.assertEqual(r.stdout.strip(), b"False")
            self.assertEqual(dump.read_bytes(), b'{"a": 1}\n')

    def test_block_stderr(self):
        r = self.run_py("ss.block_stderr('сжатие отложено')")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.decode("utf-8"), "сжатие отложено")

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / ".claude"
            (claude / "projects" / "p").mkdir(parents=True)
            (claude / "projects" / "p" / "s9.jsonl").write_text(json.dumps({
                "type": "assistant", "timestamp": "2026-10-03T12:00:00.000Z", "effort": "high",
                "message": {"model": OPUS, "usage": {"input_tokens": 5000}}}) + "\n", encoding="utf-8")
            env = {**os.environ, "CLAUDE_CONFIG_DIR": str(claude), "HOME": tmp, "USERPROFILE": tmp}
            r = subprocess.run([sys.executable, str(MODULE), "s9"], capture_output=True, env=env, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(r.stdout.isascii())
            st = json.loads(r.stdout)
            self.assertEqual((st["tokens"], st["src"]["tokens"], st["model"]), (5000, "transcript", OPUS))


if __name__ == "__main__":
    unittest.main(verbosity=1)
