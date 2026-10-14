// Проверка session-state.mjs без Claude Code: источники, свежесть, порог, лимиты, дневная норма.
// Перенос hooks/test_session_state.py один к одному; в конце — сверка с Python-версией (эталоном).
//
// Запуск: node --test hooks/test_session_state.mjs

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, it, mock } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as ss from "./session-state.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const MODULE = path.join(HERE, "session-state.mjs");
const PY_MODULE = path.join(HERE, "session_state.py");
const STATUSLINE = path.join(HERE, "..", "terminal", "bin", "claude-statusline");

const OPUS = "claude-opus-5-5";
const HAIKU = "claude-haiku-4-5-20251001";
const DAY = 86400;
// Понедельник 28.09.2026 09:00 по местному времени: начало недели лимита, сброс — через 7 дней.
const MON = new Date(2026, 8, 28, 9, 0).getTime() / 1000;
const WED = MON + 2 * DAY;
const SAT = MON + 5 * DAY;
const R7 = MON + 7 * DAY;

function iso(t) {
  return new Date(Math.floor(t * 1000)).toISOString();
}

function which(cmd) {
  if (os.platform() === "win32") return null;
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    const p = path.join(dir, cmd);
    try {
      fs.accessSync(p, fs.constants.X_OK);
      if (fs.statSync(p).isFile()) return p;
    } catch {
      // нет здесь
    }
  }
  return null;
}

function almost(a, b, delta = null) {
  // assertAlmostEqual: по умолчанию до 7 знаков, с delta — |a − b| ≤ delta.
  if (delta === null) assert.ok(Math.round((a - b) * 1e7) === 0, `${a} != ${b}`);
  else assert.ok(Math.abs(a - b) <= delta, `${a} != ${b} ± ${delta}`);
}

const keys = (obj) => Object.keys(obj).sort();
const read_json = (p) => JSON.parse(fs.readFileSync(p, "utf8"));
const write_text = (p, text) => fs.writeFileSync(p, text, "utf8");

class Case {
  // Подменённые HOME, USERPROFILE и CLAUDE_CONFIG_DIR (= <HOME>/.claude), сессия s1.

  setUp() {
    this.home = fs.mkdtempSync(path.join(os.tmpdir(), "ss-test-"));
    this.claude = path.join(this.home, ".claude");
    this.status = path.join(this.claude, "session-status");
    fs.mkdirSync(this.status, { recursive: true });
    this._env = { ...process.env };
    Object.assign(process.env, { HOME: this.home, USERPROFILE: this.home, CLAUDE_CONFIG_DIR: this.claude });
    for (const key of ["CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
      "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_EFFORT"]) delete process.env[key];
    this.sid = "s1";
    this.transcript = path.join(this.home, "s1.jsonl");
    write_text(this.transcript, "");
    this.now = Date.now() / 1000;
  }

  tearDown() {
    for (const key of Object.keys(process.env)) if (!(key in this._env)) delete process.env[key];
    Object.assign(process.env, this._env);
    fs.rmSync(this.home, { recursive: true, force: true });
  }

  // --- запись источников ---

  append(entry, compact = false) {
    const text = ss.dumps(entry, compact ? { separators: [",", ":"] } : {});
    fs.appendFileSync(this.transcript, `${text}\n`, "utf8");
  }

  answer(tokens, at, { model = OPUS, effort = "high", ...extra } = {}) {
    // Ответ модели основной ветки: в контексте `tokens` (output в счёт не идёт).
    const entry = { type: "assistant", isSidechain: false, timestamp: iso(at), ...extra,
      message: { model, usage: {
        input_tokens: 2, cache_creation_input_tokens: 1000,
        cache_read_input_tokens: tokens - 1002, output_tokens: 700 } } };
    if (effort !== null) entry.effort = effort;
    this.append(entry);
  }

  boundary(post, at) {
    this.append({ type: "system", subtype: "compact_boundary", timestamp: iso(at),
      compactMetadata: { trigger: "auto", preTokens: 967_000, postTokens: post } });
  }

  model_attachment(model, at) {
    this.append({ type: "attachment", timestamp: iso(at), attachment: {
      type: "model", identity: { modelId: model },
      text: `You are powered by the model. The exact model ID is ${model}.` } }, true);
  }

  mod(sid = null, data = {}) {
    const p = path.join(this.status, `${sid || this.sid}.state.json`);
    write_text(p, JSON.stringify({ v: 1, session_id: sid || this.sid, ...data }));
    return p;
  }

  statusline(mtime, data = {}) {
    const p = path.join(this.status, `${this.sid}.json`);
    write_text(p, JSON.stringify(data));
    fs.utimesSync(p, mtime, mtime);
  }

  inp(event = "PostToolUse", extra = {}) {
    return { session_id: this.sid, transcript_path: this.transcript, cwd: this.home, hook_event_name: event,
      ...extra };
  }

  state(event = "PostToolUse", fields = ss.FIELDS_ALL, extra = {}) {
    return ss.session_state(this.inp(event, extra), { fields, now: this.now });
  }
}

function suite(name, body) {
  describe(name, () => {
    const c = new Case();
    beforeEach(() => c.setUp());
    afterEach(() => c.tearDown());
    body(c);
  });
}

suite("FreshnessTest", (c) => {
  it("test_mod_newer_than_transcript", () => {
    c.answer(300_000, c.now - 60);
    c.mod(null, { context: { tokens: 320_000, window: 1_000_000, at: c.now - 10 } });
    const st = c.state();
    assert.deepEqual([st.tokens, st.src.tokens, st.after_compact], [320_000, "mod", false]);
  });

  it("test_transcript_newer_than_mod", () => {
    c.mod(null, { context: { tokens: 300_000, at: c.now - 60 } });
    c.answer(350_000, c.now - 5);
    const st = c.state();
    assert.deepEqual([st.tokens, st.src.tokens], [350_000, "transcript"]);
  });

  it("test_tie_goes_to_mod", () => {
    c.mod(null, { context: { tokens: 300_000, at: c.now - 10 } });
    c.answer(310_000, c.now - 9);
    const st = c.state();
    assert.deepEqual([st.tokens, st.src.tokens], [300_000, "mod"]);
  });

  it("test_boundary_sets_after_compact_until_mod_measures", () => {
    c.mod(null, { context: { tokens: 880_000, at: c.now - 200 } });
    c.answer(900_000, c.now - 100);
    c.boundary(30_000, c.now - 50);
    let st = c.state();
    assert.deepEqual([st.tokens, st.after_compact, st.src.tokens], [30_000, true, "transcript"]);
    c.mod(null, { context: { tokens: 35_000, at: c.now } });
    st = c.state();
    assert.deepEqual([st.tokens, st.after_compact, st.src.tokens], [35_000, false, "mod"]);
  });

  it("test_boundary_without_post_tokens_beats_older_mod", () => {
    // В старых транскриптах у границы бывает только preTokens: замер мода до сжатия уже неверен.
    c.mod(null, { context: { tokens: 880_000, at: c.now - 200 } });
    c.answer(900_000, c.now - 100);
    c.append({ type: "system", subtype: "compact_boundary", timestamp: iso(c.now - 50),
      compactMetadata: { trigger: "auto", preTokens: 900_000 } });
    let st = c.state();
    assert.deepEqual([st.tokens, st.after_compact, st.fill_pct], [null, true, null]);
    c.statusline(c.now, { model: OPUS, window: 1_000_000, used_pct: 0 });
    st = c.state();
    assert.deepEqual([st.tokens, st.after_compact, st.src.tokens], [0, true, "statusline~"]);
  });

  it("test_readings_before_compact_mark_are_stale", () => {
    // Первый промпт после сжатия: граница ещё не в транскрипте (Claude Code дописывает его с
    // задержкой), а отметка context-guard уже есть — последний ответ до сжатия не годится.
    c.mod(null, { context: { tokens: 318_000, at: c.now - 90 } });
    c.answer(319_000, c.now - 80);
    c.statusline(c.now, { model: OPUS, window: 1_000_000, used_pct: 31 });
    ss.note_compact(c.sid, c.now - 60);
    let st = c.state("UserPromptSubmit");
    assert.deepEqual([st.tokens, st.after_compact, st.src.tokens], [null, true, null]);
    c.boundary(28_000, c.now - 1); // граница дописана — её показание новее отметки
    st = c.state("UserPromptSubmit");
    assert.deepEqual([st.tokens, st.after_compact, st.src.tokens], [28_000, true, "transcript"]);
  });

  it("test_statusline_mtime_now_does_not_win_tokens", () => {
    c.statusline(c.now, { model: `${OPUS}[1m]`, name: "Opus 5.5 (1M context)", effort: "xhigh",
      ultracode: false, window: 1_000_000, used_pct: 40 });
    c.answer(300_000, c.now - 30);
    c.boundary(25_000, c.now - 20);
    const st = c.state("UserPromptSubmit");
    assert.deepEqual([st.tokens, st.src.tokens, st.after_compact], [25_000, "transcript", true]);
    assert.deepEqual([st.model, st.src.model], [`${OPUS}[1m]`, "statusline"]);
    assert.equal(st.model_name, "Opus 5.5 (1M context)");
    assert.deepEqual([st.effort, st.src.effort], ["xhigh", "statusline"]);
    assert.deepEqual([st.window, st.src.window], [1_000_000, "statusline"]);
  });
});

suite("RobustnessTest", (c) => {
  it("test_broken_empty_or_missing_mod", () => {
    const p = path.join(c.status, "s1.state.json");
    for (const text of ["", "{битый", "[1, 2]", '{"context": "x", "model": 5, "effort": [], "rate_limits": 1}']) {
      write_text(p, text);
      const st = c.state();
      assert.equal(st.model, null, text);
      assert.equal(st.tokens, null, text);
      assert.deepEqual(st.limits, {}, text);
    }
    fs.unlinkSync(p);
    assert.equal(ss.read_mod(c.sid), null);
  });

  it("test_nothing_at_all", () => {
    fs.unlinkSync(c.transcript);
    let st = c.state();
    for (const key of ["model", "model_name", "effort", "ultracode", "tokens", "fill_pct", "day"]) {
      assert.equal(st[key], null, key);
    }
    assert.deepEqual([st.window, st.auto_compact_at, st.limits], [1_000_000, 967_000, {}]);
    assert.equal(st.src.window, "default");
    st = ss.session_state(null);
    assert.equal(st.model, null);
    st = ss.session_state({ session_id: 5, transcript_path: ["x"], cwd: 7, effort: {} });
    assert.equal(st.tokens, null);
  });

  it("test_statusline_tokens_only_as_fallback", () => {
    c.statusline(c.now - 5, { model: HAIKU, name: "Haiku 4.5", effort: null, window: 200_000, used_pct: 30 });
    fs.unlinkSync(c.transcript);
    const st = c.state();
    assert.deepEqual([st.tokens, st.src.tokens], [60_000, "statusline~"]);
    assert.deepEqual([st.window, st.auto_compact_at, st.fill_pct], [200_000, 167_000, 35]);
    assert.equal(st.effort, null);
  });

  it("test_model_name_only_when_model_matches", () => {
    c.statusline(c.now - 100, { model: OPUS, name: "Opus 5.5", effort: "high", window: 1_000_000 });
    c.answer(100_000, c.now - 60);
    c.model_attachment(HAIKU, c.now - 5);
    let st = c.state();
    assert.deepEqual([st.model, st.src.model], [HAIKU, "transcript"]);
    assert.equal(st.model_name, HAIKU);
    c.statusline(c.now, { model: OPUS, name: "Opus 5.5", effort: "high", window: 1_000_000 });
    st = c.state();
    assert.deepEqual([st.model, st.model_name, st.src.model_name], [OPUS, "Opus 5.5", "statusline"]);
  });

  it("test_mod_model_name_ignores_1m_suffix", () => {
    c.mod(null, { model: { id: OPUS, at: c.now } });
    c.statusline(c.now - 60, { model: `${OPUS}[1m]`, name: "Opus 5.5 (1M context)" });
    const st = c.state();
    assert.deepEqual([st.model, st.model_name], [OPUS, "Opus 5.5 (1M context)"]);
  });
});

suite("ModelEffortTest", (c) => {
  it("test_synthetic_skipped", () => {
    c.answer(300_000, c.now - 50, { effort: "xhigh" });
    c.append({ type: "assistant", timestamp: iso(c.now - 10), effort: null, message: {
      model: "<synthetic>", usage: { input_tokens: 0, output_tokens: 0 } } });
    const st = c.state("UserPromptSubmit");
    assert.deepEqual([st.model, st.tokens, st.effort], [OPUS, 300_000, "xhigh"]);
  });

  it("test_sidechain_skipped", () => {
    c.answer(300_000, c.now - 50);
    c.answer(50_000, c.now - 10, { model: HAIKU, isSidechain: true });
    const st = c.state();
    assert.deepEqual([st.model, st.tokens], [OPUS, 300_000]);
  });

  it("test_model_attachment_after_switch_beats_old_answer", () => {
    c.answer(150_000, c.now - 50);
    c.model_attachment(HAIKU, c.now - 20);
    const st = c.state();
    assert.deepEqual([st.model, st.src.model], [HAIKU, "transcript"]);
    assert.deepEqual([st.window, st.auto_compact_at], [200_000, 167_000]);
  });

  it("test_effort_from_hook_input", () => {
    c.answer(100_000, c.now - 5, { effort: "high" });
    const st = c.state("PostToolUse", ss.FIELDS_ALL, { effort: { level: "max" } });
    assert.deepEqual([st.effort, st.src.effort], ["max", "hook"]);
  });

  it("test_effort_env_not_on_prompt", () => {
    process.env.CLAUDE_EFFORT = "low";
    c.answer(100_000, c.now - 5, { effort: "high" });
    let st = c.state("UserPromptSubmit");
    assert.deepEqual([st.effort, st.src.effort], ["high", "transcript"]);
    st = c.state("PostToolUse");
    assert.deepEqual([st.effort, st.src.effort], ["low", "env"]);
  });

  it("test_effort_from_transcript_without_mod", () => {
    c.answer(100_000, c.now - 5, { effort: "xhigh" });
    const st = ss.read_model(c.inp("UserPromptSubmit"));
    assert.deepEqual([st.effort, st.src.effort], ["xhigh", "transcript"]);
    assert.deepEqual(keys(st), ["effort", "model", "model_name", "src"]);
  });

  it("test_effort_from_mod_when_newer", () => {
    c.answer(100_000, c.now - 60, { effort: "high" });
    c.mod(null, { effort: { level: "medium", at: c.now - 1 }, model: { id: OPUS, at: c.now - 1 } });
    const st = c.state("UserPromptSubmit");
    assert.deepEqual([st.effort, st.src.effort, st.src.model], ["medium", "mod", "mod"]);
  });
});

suite("ThresholdTest", (c) => {
  it("test_threshold_priority", () => {
    c.answer(400_000, c.now - 5);
    const mod = c.mod(null, { context: { auto_compact_at: 400_000, at: c.now - 100 } });
    let st = c.state();
    assert.deepEqual([st.auto_compact_at, st.src.auto_compact_at, st.fill_pct], [400_000, "mod", 100]);
    assert.equal(st.auto_compact_enabled, true);
    fs.unlinkSync(mod);
    process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = "600k";
    const project = path.join(c.home, "proj", ".claude");
    fs.mkdirSync(project, { recursive: true });
    write_text(path.join(project, "settings.local.json"), '{"autoCompactWindow": 500000}');
    write_text(path.join(project, "settings.json"), '{"autoCompactWindow": "700k"}');
    write_text(path.join(c.claude, "settings.json"), '{"autoCompactWindow": 800000}');
    const cwd = path.join(c.home, "proj");
    const expect = [[567_000, "env"], [467_000, "settings"], [667_000, "settings"], [767_000, "settings"],
      [967_000, "default"]];
    const removals = [() => delete process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW,
      () => fs.unlinkSync(path.join(project, "settings.local.json")),
      () => fs.unlinkSync(path.join(project, "settings.json")),
      () => fs.unlinkSync(path.join(c.claude, "settings.json"))];
    expect.forEach(([value, src], i) => {
      st = ss.read_context(c.inp(), { cwd });
      assert.deepEqual([st.auto_compact_at, st.src.auto_compact_at], [value, src], String(i));
      assert.equal(st.auto_compact_enabled, null);
      if (i < removals.length) removals[i]();
    });
  });

  it("test_200k_model_thresholds", () => {
    assert.equal(ss.autocompact_at(HAIKU, null), 167_000);
    assert.equal(ss.window_for(HAIKU), 200_000);
    process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = "500000";
    assert.equal(ss.autocompact_at(OPUS, null), 467_000);
    process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW = "2m"; // явный больше окна — по окну
    assert.equal(ss.autocompact_at(HAIKU, null), 167_000);
    delete process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW;
    assert.equal(ss.autocompact_at(OPUS, null), 967_000);
    assert.equal(ss.autocompact_at(null, null), 967_000);
    assert.equal(ss.AUTOCOMPACT_RESERVE, 33_000);
  });

  it("test_window_env_and_mod", () => {
    process.env.CLAUDE_CODE_DISABLE_1M_CONTEXT = "1";
    c.answer(100_000, c.now - 5);
    let st = c.state();
    assert.deepEqual([st.window, st.src.window, st.auto_compact_at], [200_000, "env", 167_000]);
    c.mod(null, { context: { window: 1_000_000, auto_compact_at: null, auto_compact_enabled: false, at: c.now } });
    st = c.state();
    assert.deepEqual([st.window, st.src.window], [1_000_000, "mod"]);
    assert.deepEqual([st.auto_compact_at, st.auto_compact_enabled, st.fill_pct], [null, false, null]);
    assert.equal(st.src.auto_compact_enabled, "mod");
  });
});

suite("LimitsTest", (c) => {
  const write_rate_limits = (ts, windows) => {
    write_text(path.join(c.status, "rate-limits.json"), JSON.stringify({ ...windows, ts }));
  };

  it("test_merge_mod_files_and_statusline", () => {
    const r5 = c.now + 3600;
    const r7 = c.now + 3 * DAY;
    c.mod("a", { ts: c.now - 300, rate_limits: {
      five_hour: { used_percentage: 10, resets_at: r5, at: c.now - 300 },
      seven_day: { used_percentage: 40, resets_at: r7, at: c.now - 300 } } });
    c.mod("b", { rate_limits: { five_hour: { used_percentage: 25, resets_at: r5, at: c.now - 60 } } });
    write_rate_limits(c.now - 120, { five_hour: { used_percentage: 20, resets_at: r5 },
      seven_day: { used_percentage: 45, resets_at: r7 } });
    const lim = ss.read_limits(c.now);
    assert.deepEqual([lim.five_hour.used, lim.five_hour.src], [25, "mod"]);
    almost(lim.five_hour.age, 60, 1);
    assert.deepEqual([lim.seven_day.used, lim.seven_day.src], [45, "statusline"]);
    almost(lim.seven_day.age, 120, 1);
    assert.equal(lim.seven_day.resets_at, r7);
  });

  it("test_passed_reset_dropped_and_old_files_ignored", () => {
    write_rate_limits(c.now - 4000, { five_hour: { used_percentage: 90, resets_at: c.now - 10 },
      seven_day: { used_percentage: 50, resets_at: c.now + DAY } });
    const old = c.mod("old", { rate_limits: { seven_day: { used_percentage: 99, resets_at: c.now + DAY,
      at: c.now - 9 * DAY } } });
    fs.utimesSync(old, c.now - 9 * DAY, c.now - 9 * DAY);
    const lim = ss.read_limits(c.now);
    assert.ok(!("five_hour" in lim));
    assert.equal(lim.seven_day.used, 50);
    assert.deepEqual(ss.session_state(null, { fields: ["limits"], now: c.now }).limits, lim);
  });

  it("test_old_reading_valid_until_reset", () => {
    write_rate_limits(c.now - 3600, { five_hour: { used_percentage: 92, resets_at: c.now + 600 } });
    const lim = ss.read_limits(c.now);
    assert.equal(lim.five_hour.used, 92);
    almost(lim.five_hour.age, 3600, 1);
  });

  it("test_reset_rounded_to_minute", () => {
    const r7 = 1_791_352_800; // ровный час, как у настоящих сбросов
    c.mod("a", { rate_limits: { seven_day: { used_percentage: 50, resets_at: r7 + 0.4, at: c.now } } });
    write_rate_limits(c.now - 500, { seven_day: { used_percentage: 52, resets_at: r7 } });
    const lim = ss.read_limits(r7 - DAY);
    assert.deepEqual([lim.seven_day.used, lim.seven_day.src], [52, "statusline"]);
  });

  it("test_nothing", () => {
    fs.rmSync(c.status, { recursive: true });
    assert.deepEqual(ss.read_limits(), {});
  });
});

suite("DayBudgetTest", (c) => {
  const budget_path = () => path.join(c.status, "week-budget.json");
  const budget = () => (fs.existsSync(budget_path()) ? read_json(budget_path()) : null);
  const set_budget = (day_start, start_pct) => {
    write_text(budget_path(), JSON.stringify({ day_start, start_pct }));
  };

  it("test_first_day_of_week", () => {
    const day = ss.day_budget({ used: 4, resets_at: R7, at: MON + 3600 }, MON + 3600);
    assert.deepEqual([day.start, day.spent, day.norm, day.day_start], [0, 4, 20, MON]);
    assert.deepEqual(budget(), { day_start: MON, start_pct: 0 });
  });

  it("test_weekday_from_file", () => {
    set_budget(WED, 30);
    const day = ss.day_budget({ used: 35, resets_at: R7, at: WED + 3600 }, WED + 3600);
    assert.deepEqual([day.start, day.spent], [30, 5]);
    almost(day.norm, 70 / 3);
    assert.deepEqual(budget(), { day_start: WED, start_pct: 30 });
  });

  it("test_new_day_writes_file", () => {
    set_budget(WED - DAY, 20);
    const day = ss.day_budget({ used: 33, resets_at: R7, at: WED + 1800 }, WED + 3600);
    assert.deepEqual([day.start, day.spent, day.day_start], [33, 0, WED]);
    almost(day.norm, 67 / 3);
    assert.deepEqual(budget(), { day_start: WED, start_pct: 33 });
  });

  it("test_weekend_is_one_day", () => {
    const day = ss.day_budget({ used: 70, resets_at: R7, at: SAT + 60 }, SAT + 3600);
    assert.deepEqual([day.start, day.norm], [70, 30]);
  });

  it("test_yesterdays_reading_not_written", () => {
    set_budget(WED - DAY, 20);
    const day = ss.day_budget({ used: 30, resets_at: R7, at: WED - 600 }, WED + 600);
    assert.deepEqual([day.start, day.spent, day.day_start], [30, 0, WED]);
    assert.deepEqual(budget(), { day_start: WED - DAY, start_pct: 20 });
  });

  it("test_no_data", () => {
    assert.equal(ss.day_budget(null), null);
    assert.equal(ss.day_budget({ used: 5 }), null);
    assert.equal(c.state().day, null);
  });

  it("test_session_state_day_from_limits", () => {
    write_text(path.join(c.status, "rate-limits.json"), JSON.stringify(
      { seven_day: { used_percentage: 35, resets_at: R7 }, ts: WED + 100 }));
    set_budget(WED, 30);
    const st = ss.session_state(null, { fields: ["day"], now: WED + 3600 });
    assert.deepEqual(keys(st), ["day", "src"]);
    assert.equal(st.day.spent, 5);
  });

  it("test_matches_statusline", { skip: !(which("bash") && which("jq")) && "нужны bash и jq" }, () => {
    const now = Date.now() / 1000;
    const reset = Math.trunc(now + 3.5 * DAY); // середина суток лимита: граница дня далеко
    const ws = reset - 7 * DAY;
    const ds = ws + Math.floor((now - ws) / DAY) * DAY;
    const inp = { session_id: "s1", model: { id: OPUS, display_name: "Opus 5.5" },
      rate_limits: { seven_day: { used_percentage: 40, resets_at: reset } } };

    const statusline = () => {
      const r = spawnSync("bash", [STATUSLINE], { input: JSON.stringify(inp), env: { ...process.env, HOME: c.home },
        timeout: 30_000 });
      const m = /сегодня \x1b\[[0-9;]*m(\d+)\/(\d+)%/.exec(r.stdout.toString("utf8"));
      assert.ok(m, r.stdout.toString("utf8") + r.stderr.toString("utf8"));
      return [Number(m[1]), Number(m[2])];
    };

    const d7 = { used: 40, resets_at: reset, at: now };
    // Начало дня уже записано: обе стороны берут его из файла.
    set_budget(ds, 31);
    let shown = statusline();
    let day = ss.day_budget(d7, now);
    assert.deepEqual(shown, [Math.floor(day.spent), Math.floor(day.norm)]);
    assert.equal(day.start, 31);
    // Файла нет: обе стороны записывают одно и то же.
    fs.unlinkSync(budget_path());
    shown = statusline();
    const written = budget();
    fs.unlinkSync(budget_path());
    day = ss.day_budget(d7, now);
    assert.deepEqual(budget(), written);
    assert.deepEqual(shown, [Math.floor(day.spent), Math.floor(day.norm)]);
  });
});

suite("LazyAndSpeedTest", (c) => {
  it("test_fields_limits_does_not_touch_transcript", () => {
    fs.unlinkSync(c.transcript);
    fs.mkdirSync(c.transcript); // каталог вместо файла
    // Вместо счётчиков вызовов entries_backwards и _ultra_scan (функции ESM не подменить) — ни одного
    // обращения к файловой системе по пути транскрипта.
    const touched = [];
    const spies = ["openSync", "statSync", "readFileSync", "opendirSync"].map((name) => {
      const orig = fs[name];
      return mock.method(fs, name, function (p, ...rest) {
        if (p === c.transcript) touched.push(name);
        return orig.call(this, p, ...rest);
      });
    });
    try {
      let st = ss.session_state(c.inp(), { fields: ["limits"] });
      assert.deepEqual(keys(st), ["limits", "src"]);
      assert.deepEqual(touched, []);
      st = ss.session_state(c.inp()); // и полный набор на каталоге — без ошибки
      assert.equal(st.tokens, null);
      assert.equal(st.ultracode, null);
    } finally {
      for (const spy of spies) spy.mock.restore();
    }
  });

  it("test_big_transcript_read_fast", () => {
    c.answer(200_000, c.now - 3600);
    const filler = Buffer.from(`${ss.dumps({ type: "user", message: { role: "user", content: [
      { type: "tool_result", content: "x".repeat(100_000) }] } })}\n`);
    fs.appendFileSync(c.transcript, Buffer.concat(Array(500).fill(filler))); // ~50 МБ
    c.answer(420_000, c.now - 5);
    assert.ok(fs.statSync(c.transcript).size > 50_000_000);
    const t0 = performance.now();
    ss.read_limits();
    const st = ss.read_context(c.inp());
    const elapsed = (performance.now() - t0) / 1000;
    assert.equal(st.tokens, 420_000);
    assert.ok(elapsed < 0.5, String(elapsed));
  });

  it("test_ultracode_reads_only_growth", () => {
    const enter = { type: "attachment", attachment: { type: "ultra_effort_enter" } };
    c.append(enter, true);
    c.answer(100_000, c.now - 5);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, true);
    const cache = path.join(c.status, "s1.ultra.json");
    const first = read_json(cache);
    assert.equal(first.offset, fs.statSync(c.transcript).size);
    // Прочитанное начало больше не перечитывается: стираем в нём событие, той же длиной.
    const data = fs.readFileSync(c.transcript, "latin1").replace("ultra_effort_enter", "ultra_effort_xxxxx");
    fs.writeFileSync(c.transcript, data, "latin1");
    c.answer(110_000, c.now - 1);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, true);
    const second = read_json(cache);
    assert.ok(second.offset > first.offset);
    assert.equal(second.offset, fs.statSync(c.transcript).size);
    // Файл стал короче offset — полный проход: событий больше нет.
    write_text(c.transcript, "");
    c.answer(5_000, c.now);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, false);
  });

  it("test_ultracode_partial_line_read_later", () => {
    c.answer(100_000, c.now - 5);
    fs.appendFileSync(c.transcript, '{"type":"attachment","attachment":{"type":"ultra_eff'); // строка ещё пишется
    assert.equal(ss.read_ultracode(c.inp()).ultracode, false);
    fs.appendFileSync(c.transcript, 'ort_enter"}}\n');
    assert.equal(ss.read_ultracode(c.inp()).ultracode, true);
  });
});

suite("UltracodeTest", (c) => {
  const command = (text) => {
    c.append({ type: "user", message: { role: "user", content: `<local-command-stdout>${text}` } }, true);
  };

  it("test_on_off", () => {
    command("Ultracode on: Opus 5.5, max effort");
    const st = ss.read_ultracode(c.inp());
    assert.deepEqual([st.ultracode, st.src.ultracode], [true, "transcript"]);
    c.append({ type: "attachment", attachment: { type: "ultra_effort_exit" } }, true);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, false);
    command("Ultracode on");
    assert.equal(ss.read_ultracode(c.inp()).ultracode, true);
    command("Ultracode off.");
    assert.equal(ss.read_ultracode(c.inp()).ultracode, false);
  });

  it("test_no_events", () => {
    c.answer(100_000, c.now);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, false);
  });

  it("test_no_file", () => {
    fs.unlinkSync(c.transcript);
    assert.equal(ss.read_ultracode(c.inp()).ultracode, null);
    c.statusline(c.now, { model: OPUS, ultracode: true });
    const st = ss.read_ultracode(c.inp());
    assert.deepEqual([st.ultracode, st.src.ultracode], [true, "statusline"]);
  });
});

suite("BricksTest", (c) => {
  it("test_parse_tokens", () => {
    const cases = [["900000", 900_000], ["900k", 900_000], ["900K", 900_000], ["0.9m", 900_000],
      ["1.2M", 1_200_000], ["650к", 650_000], ["0,65М", 650_000], [" 650 k ", 650_000], ["650", 650_000],
      ["100", 100_000], ["5000", 5_000], [900_000, 900_000], [500_000.0, 500_000], ["0.57m", 570_000]];
    for (const [raw, value] of cases) assert.equal(ss.parse_tokens(raw), value, String(raw));
    for (const raw of ["auto", "", null, "abc", "12q", "-5k", 0, -1, true, [], "0"]) {
      assert.equal(ss.parse_tokens(raw), null, String(raw));
    }
  });

  it("test_write_json_replace_fails_quietly", () => {
    const target = path.join(c.status, "x.json");
    const spy = mock.method(fs, "renameSync", () => {
      throw Object.assign(new Error("занят"), { code: "EPERM" });
    });
    try {
      assert.equal(ss.write_json(target, { a: 1 }), false);
    } finally {
      spy.mock.restore();
    }
    assert.ok(!fs.existsSync(target));
    assert.deepEqual(fs.readdirSync(c.status).filter((n) => n.startsWith("x.json.tmp.")), []);
    assert.equal(ss.write_json(path.join(c.status, "new", "y.json"), { "текст": "да" }), true);
    assert.deepEqual(read_json(path.join(c.status, "new", "y.json")), { "текст": "да" });
  });

  it("test_emit_context_ascii", () => {
    let text = "";
    const orig = fs.writeSync;
    const spy = mock.method(fs, "writeSync", (fd, buf, off = 0, len = buf.length - off, ...rest) => {
      if (fd !== 1) return orig.call(fs, fd, buf, off, len, ...rest);
      text += Buffer.from(buf).subarray(off, off + len).toString("utf8");
      return len;
    });
    try {
      ss.emit_context("UserPromptSubmit", "контекст 300k — проверка");
    } finally {
      spy.mock.restore();
    }
    assert.ok(/^[\x00-\x7f]*$/.test(text), text);
    assert.equal(JSON.parse(text).hookSpecificOutput.additionalContext, "контекст 300k — проверка");
  });

  it("test_paths_follow_home_and_userprofile", () => {
    delete process.env.CLAUDE_CONFIG_DIR;
    assert.equal(ss.claude_dir(), path.join(c.home, ".claude"));
    assert.equal(ss.status_dir(), c.status);
    assert.equal(ss.safe_sid("a/b c-1_2"), "a_b_c-1_2");
    assert.equal(ss.safe_sid(null), "unknown");
  });

  it("test_cleanup_status", () => {
    const old = c.now - 31 * DAY;
    const fresh = c.now - DAY;
    const names = ["a.state.json", "a.ultra.json", "a.ctx-mark", "a.json.tmp.1", "a.json", "b.state.json"];
    for (const name of names) {
      write_text(path.join(c.status, name), "{}");
      const t = name.startsWith("b") ? fresh : old;
      fs.utimesSync(path.join(c.status, name), t, t);
    }
    ss.cleanup_status();
    assert.deepEqual(fs.readdirSync(c.status).sort(), ["a.json", "b.state.json"]);
  });

  it("test_entries_backwards_long_lines_and_limit", () => {
    c.append({ type: "user", n: 1, pad: "y".repeat(3 << 20) });
    c.append({ type: "user", n: 2 });
    assert.deepEqual([...ss.entries_backwards(c.transcript)].map((e) => e.n), [2, 1]);
    assert.deepEqual([...ss.entries_backwards(c.transcript, 1 << 20)].map((e) => e.n), [2]);
    assert.deepEqual([...ss.entries_backwards(c.home)], []);
    assert.deepEqual([...ss.entries_backwards(null)], []);
  });
});

describe("ProcessTest", () => {
  // Ввод-вывод хука в отдельном процессе: как его увидят хуки.

  const run_node = (code, stdin = "", env = {}) => {
    const prog = `import * as ss from ${JSON.stringify(pathToFileURL(MODULE).href)};\n${code}`;
    return spawnSync(process.execPath, ["--input-type=module", "-e", prog], { input: stdin, timeout: 30_000,
      env: { ...process.env, ...env } });
  };

  it("test_read_input", () => {
    const code = "console.log(JSON.stringify(ss.read_input()))";
    assert.equal(run_node(code, "").stdout.toString().trim(), "null");
    assert.equal(run_node(code, "{not json").stdout.toString().trim(), "null");
    assert.equal(run_node(code, "[1, 2]").stdout.toString().trim(), "null");
    assert.equal(run_node(code, Buffer.from([0xff, 0xfe])).stdout.toString().trim(), "null");
    const r = run_node(code, Buffer.from(JSON.stringify({ prompt: "привет" }), "utf8"));
    assert.equal(r.stdout.toString("utf8").trim(), '{"prompt":"привет"}');
  });

  it("test_dump_input", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ss-test-"));
    try {
      const dump = path.join(tmp, "dump.jsonl");
      const r = run_node("console.log(ss.read_input('X_DUMP') === null)", '{"a": 1}', { X_DUMP: dump });
      assert.equal(r.stdout.toString().trim(), "false");
      assert.equal(fs.readFileSync(dump, "latin1"), '{"a": 1}\n');
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("test_block_stderr", () => {
    const r = run_node("ss.block_stderr('сжатие отложено')");
    assert.equal(r.status, 2);
    assert.equal(r.stderr.toString("utf8"), "сжатие отложено");
  });

  it("test_cli", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ss-test-"));
    try {
      const claude = path.join(tmp, ".claude");
      fs.mkdirSync(path.join(claude, "projects", "p"), { recursive: true });
      write_text(path.join(claude, "projects", "p", "s9.jsonl"), `${JSON.stringify({
        type: "assistant", timestamp: "2026-10-03T12:00:00.000Z", effort: "high",
        message: { model: OPUS, usage: { input_tokens: 5000 } } })}\n`);
      const env = { ...process.env, CLAUDE_CONFIG_DIR: claude, HOME: tmp, USERPROFILE: tmp };
      const r = spawnSync(process.execPath, [MODULE, "s9"], { env, timeout: 30_000 });
      assert.equal(r.status, 0, r.stderr.toString());
      assert.ok(/^[\x00-\x7f]*$/.test(r.stdout.toString("latin1")));
      const st = JSON.parse(r.stdout.toString());
      assert.deepEqual([st.tokens, st.src.tokens, st.model], [5000, "transcript", OPUS]);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  });
});

// --- сверка с Python-версией ---------------------------------------------------------------------
// Не из test_session_state.py: одни и те же выдуманные сессии через обе версии. Совпасть должны
// stdout отладочного CLI (кроме age — зависит от текущего времени) и файлы, которые они пишут.

const PYTHON = which("python3");

function run_cli(cmd, args, home) {
  const env = { ...process.env, HOME: home, USERPROFILE: home, CLAUDE_CONFIG_DIR: path.join(home, ".claude"),
    PYTHONIOENCODING: "utf-8" };
  for (const key of ["CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
    "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_EFFORT"]) delete env[key];
  const r = spawnSync(cmd, args, { env, timeout: 30_000 });
  assert.equal(r.status, 0, r.stderr.toString());
  return r.stdout.toString("utf8");
}

function snapshot(dir) {
  // Файлы состояния, кроме заготовленных: JSON разобранным.
  const out = {};
  for (const name of fs.readdirSync(dir).sort()) out[name] = read_json(path.join(dir, name));
  return out;
}

function without_age(st) {
  for (const w of Object.values(st.limits || {})) {
    assert.equal(typeof w.age, "number");
    w.age = "~";
  }
  return st;
}

suite("CrossCheckTest", (c) => {
  const skip = !PYTHON && "нужен python3";

  const compare = (sid, transcript = null) => {
    // Python, потом откат записанного, потом Node — на тех же файлах.
    const args = transcript ? [sid, transcript] : [sid];
    const before = new Set(fs.readdirSync(c.status));
    const strip = () => {
      const written = {};
      for (const name of fs.readdirSync(c.status)) {
        if (before.has(name)) continue;
        written[name] = read_json(path.join(c.status, name));
        fs.unlinkSync(path.join(c.status, name));
      }
      return written;
    };
    const py_out = run_cli(PYTHON, [PY_MODULE, ...args], c.home);
    const py_files = strip();
    const node_out = run_cli(process.execPath, [MODULE, ...args], c.home);
    const node_files = strip();
    const py = JSON.parse(py_out);
    const node = JSON.parse(node_out);
    assert.deepEqual(without_age(node), without_age(py));
    assert.deepEqual(node_files, py_files);
    return [py, node_out, py_out];
  };

  it("сессия из транскрипта: сжатие, модель, ultracode", { skip }, () => {
    c.answer(300_000, c.now - 300);
    c.boundary(41_000, c.now - 200);
    c.model_attachment(HAIKU, c.now - 150);
    c.append({ type: "attachment", attachment: { type: "ultra_effort_enter" } }, true);
    c.answer(52_345, c.now - 100, { model: HAIKU, effort: null });
    const [st] = compare(c.sid, c.transcript);
    assert.deepEqual([st.model, st.tokens, st.ultracode, st.window], [HAIKU, 52_345, true, 200_000]);
  });

  it("мод, статусная строка и лимиты", { skip }, () => {
    c.answer(410_000, c.now - 40, { effort: "xhigh" });
    c.mod(null, { ts: c.now - 20, model: { id: OPUS, at: c.now - 20 }, effort: { level: "max", at: c.now - 20 },
      context: { tokens: 412_000, window: 1_000_000, auto_compact_at: 600_000, auto_compact_enabled: true },
      rate_limits: { five_hour: { used_percentage: 37.5, resets_at: c.now + 7200 },
        seven_day: { used_percentage: 61, resets_at: Math.trunc(c.now / 3600) * 3600 + 2 * DAY } } });
    c.statusline(c.now - 1.25, { model: `${OPUS}[1m]`, name: "Opus 5.5 (1M context)", effort: "high",
      ultracode: false, window: 1_000_000, used_pct: 41 });
    write_text(path.join(c.status, "rate-limits.json"), JSON.stringify({
      five_hour: { used_percentage: 39, resets_at: c.now + 7200 }, ts: c.now - 30 }));
    const [st, node_out, py_out] = compare(c.sid, c.transcript);
    assert.deepEqual([st.src.tokens, st.fill_pct, st.model_name], ["mod", 68, "Opus 5.5 (1M context)"]);
    // Вывод совпадает и побайтно (indent=1, ensure_ascii), кроме age и целых float: Python пишет «13.0».
    const plain = (s) => s.replace(/"age": [^,\n]+/g, '"age": ~').replace(/(\d)\.0(?=,?\n)/g, "$1");
    assert.equal(plain(node_out), plain(py_out));
  });

  it("статусная строка без транскрипта, поиск транскрипта по session_id", { skip }, () => {
    c.statusline(c.now - 3, { model: HAIKU, name: "Haiku 4.5", effort: null, window: 200_000, used_pct: 12.5 });
    const project = path.join(c.claude, "projects", "-tmp-x");
    fs.mkdirSync(project, { recursive: true });
    fs.renameSync(c.transcript, path.join(project, "s1.jsonl"));
    c.transcript = path.join(project, "s1.jsonl");
    c.append({ type: "user", message: { role: "user", content: "<local-command-stdout>Ultracode on" } }, true);
    const [st] = compare(c.sid);
    assert.deepEqual([st.tokens, st.src.tokens, st.ultracode], [25_000, "statusline~", true]);
  });

  it("битые и пустые источники, неизвестная сессия", { skip }, () => {
    write_text(path.join(c.status, "s1.state.json"), "{битый");
    write_text(path.join(c.status, "s1.json"), "[1, 2]");
    write_text(c.transcript, '{"type": "assistant", "message": 5}\nмусор\n\n');
    compare(c.sid, c.transcript);
    compare("нет-такой/сессии");
  });

  it("кирпичи: разбор чисел, времени, session_id", { skip }, () => {
    const values = ["900k", "0,65М", " 650 k ", "٣٠٠k", "0.0005k", "0.0015k", "2.5", "4999.5", "1e3", "",
      "2026-10-03T12:00:00.000Z", "2026-10-03T12:00:00.", "2026-10-03T24:00:00Z", "2026-10-03T12:00:00:5Z",
      "20261003T120000Z", "2026-W40-6T12:00Z", "2026-W40T12", "2026W406", "2026-10-03Т12:00Z",
      "2026-10-03 12:00:00.1234567+05:30", "2026-10-03T12:00+00:00:00.5", "2026-10-03T12:00-23:59:59.999999",
      "2026-10-03T12:00+24:00", "2026-10-03", "2026-10-03T12", "2026-02-29T00:00Z", "2024-02-29T00:00Z",
      "0000-W52-7", "9999-12-31T23:59:59.999999Z", "0001-01-01T00:00Z", "2026-03-29T02:30", "2026-10-25T02:30",
      "2026-10-03T1200", "2026-10-03T12:0000", "a/b c-1_2", "x y ", 5, 2.5, null, true, ["x", "a'b"],
      { "к": [1, null] }];
    const py = `import json, sys; sys.path.insert(0, ${JSON.stringify(HERE)}); import session_state as ss
vals = json.loads(sys.stdin.read())
print(json.dumps([[ss.parse_tokens(v) if not isinstance(v, (list, dict)) else None, ss._iso_ts(v), ss.safe_sid(v)]
                  for v in vals]))`;
    const r = spawnSync(PYTHON, ["-c", py], { input: JSON.stringify(values), timeout: 30_000,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    assert.equal(r.status, 0, r.stderr.toString());
    const expected = JSON.parse(r.stdout.toString("utf8"));
    const got = values.map((v) => [typeof v === "object" && v !== null ? null : ss.parse_tokens(v), ss._iso_ts(v),
      ss.safe_sid(v)]);
    values.forEach((v, i) => assert.deepEqual(got[i], expected[i], JSON.stringify(v)));
  });
});
