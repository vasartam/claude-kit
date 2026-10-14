"""Проверка link.py без настоящего ~/.claude: копия набора с выдуманными слоями и пустой каталог
настроек (CLAUDE_CONFIG_DIR).

Запуск: python scripts/test_link.py
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().with_name("link.py")


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Kit:
    """Набор во временном каталоге: общий слой и terminal, macos, desktop.

    HOME и USERPROFILE — сам временный каталог: ~/kit/… в ручной записи указывает на набор
    (на Windows expanduser берёт USERPROFILE).
    """

    def __init__(self, tmp):
        tmp = Path(tmp).resolve()  # macOS: /var — ссылка на /private/var
        self.root = tmp / "kit"
        self.claude = tmp / "claude"
        write(self.root / "scripts" / "link.py", SOURCE.read_text("utf-8"))
        write(self.root / "USER-CLAUDE.md", "общие правила\n")
        write(self.root / "skills" / "dev-method" / "SKILL.md", "---\nname: dev-method\n---\n")
        write(self.root / "hooks" / "msk_time.py", "")
        write(self.root / "hooks" / "hooks.json", json.dumps({"hooks": {
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/msk_time.py" prompt'}]}],
            "Stop": [{"hooks": [
                {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/msk_time.py" stop',
                 "timeout": 10}]}]}}))
        write(self.root / "terminal" / "USER-CLAUDE.md", "терминал\n")
        write(self.root / "terminal" / "skills" / "restart-session" / "SKILL.md", "x")
        write(self.root / "terminal" / "hooks" / "state.sh", "")
        write(self.root / "terminal" / "hooks" / "guard.py", "")
        write(self.root / "terminal" / "hooks" / "hooks.json", json.dumps({"hooks": {
            "UserPromptSubmit": [{"hooks": [
                {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"'}]}],
            "SessionStart": [
            {"matcher": "startup", "hooks": [
                {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/state.sh" session',
                 "timeout": 5, "asyncRewake": True}]}]}}))
        write(self.root / "terminal" / "commands" / "restart-cmd.md", "x")
        tool = write(self.root / "terminal" / "bin" / "claude-close", "#!/bin/sh\n")
        tool.chmod(0o755)
        write(self.root / "desktop" / "skills" / "night-work" / "SKILL.md", "x")
        for mod in (self.root / "mods" / "session-state", self.root / "terminal" / "mods" / "agent-view-status"):
            write(mod / ".claude-plugin" / "plugin.json", json.dumps({"name": mod.name, "version": "0.1.0"}))
            write(mod / "hooks" / "hooks.json", json.dumps({"modules": ["./register.ts"]}))
        self.mods = [self.root / "mods" / "session-state", self.root / "terminal" / "mods" / "agent-view-status"]
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.claude), "HOME": str(tmp), "USERPROFILE": str(tmp)}

    def run(self, *args, cwd=None):
        out = subprocess.run([sys.executable, str(self.root / "scripts" / "link.py"), *args],
                             env=self.env, capture_output=True, text=True, encoding="utf-8", cwd=cwd)
        if out.returncode:
            raise AssertionError(out.stdout + out.stderr)
        return out.stdout

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text("utf-8"))

    def memory(self):
        return (self.claude / "CLAUDE.md").read_text("utf-8")

    def mod_dirs(self):
        """Что записано в env.CLAUDE_CODE_PLUGIN_DIRS, списком; ключа нет — пусто."""
        value = self.settings().get("env", {}).get("CLAUDE_CODE_PLUGIN_DIRS", "")
        return value.split(os.pathsep) if value else []

    def set_config(self, **fields):
        config = self.claude / "claude-kit.json"
        data = json.loads(config.read_text("utf-8"))
        data.update(fields)
        config.write_text(json.dumps(data), "utf-8")


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.kit = Kit(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_without_config_explains_first_run(self):
        out = subprocess.run([sys.executable, str(self.kit.root / "scripts" / "link.py")],
                             env=self.kit.env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("--init", out.stderr)

    @unittest.skipIf(sys.platform == "win32", "слой terminal (bin, commands, bash) и symlink — не для Windows")
    def test_init_links_selected_layers_only(self):
        self.kit.run("--init", "terminal")
        skills = self.kit.claude / "skills"
        self.assertEqual(sorted(p.name for p in skills.iterdir()), ["dev-method", "restart-session"])
        self.assertEqual(Path(os.readlink(skills / "restart-session")),
                         self.kit.root / "terminal" / "skills" / "restart-session")
        self.assertTrue((self.kit.claude / "bin" / "claude-close").is_symlink())
        self.assertEqual(Path(os.readlink(self.kit.claude / "commands" / "restart-cmd.md")),
                         self.kit.root / "terminal" / "commands" / "restart-cmd.md")
        memory = self.kit.memory()
        self.assertIn(f"@{(self.kit.root / 'USER-CLAUDE.md').as_posix()}", memory)
        self.assertIn(f"@{(self.kit.root / 'terminal' / 'USER-CLAUDE.md').as_posix()}", memory)
        hooks = self.kit.settings()["hooks"]
        self.assertEqual(hooks["Stop"][0]["hooks"][0]["args"],
                         [(self.kit.root / "hooks" / "msk_time.py").as_posix(), "stop"])
        self.assertEqual(hooks["Stop"][0]["hooks"][0]["command"], sys.executable)
        start = hooks["SessionStart"][0]
        self.assertEqual(start["matcher"], "startup")
        self.assertEqual(start["hooks"][0]["command"], "/bin/bash")
        self.assertEqual(start["hooks"][0]["args"],
                         [(self.kit.root / "terminal" / "hooks" / "state.sh").as_posix(), "session"])
        self.assertEqual(start["hooks"][0]["timeout"], 5)
        self.assertIs(start["hooks"][0]["asyncRewake"], True)

    def test_second_run_changes_nothing(self):
        self.kit.run("--init", "terminal")
        before = (self.kit.settings(), self.kit.memory())
        self.assertIn("Без изменений", self.kit.run())
        self.assertIn("Без изменений", self.kit.run("--check"))  # хуки одного события из двух слоёв
        self.assertEqual((self.kit.settings(), self.kit.memory()), before)

    def test_foreign_entries_untouched(self):
        write(self.kit.claude / "CLAUDE.md", "# мои правила\n@/elsewhere/USER-CLAUDE.md\nконец\n")
        write(self.kit.claude / "settings.json", json.dumps({"model": "opus", "hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "/usr/local/bin/foreign.sh"}]}]}}))
        foreign_skill = write(self.kit.claude / "skills" / "mine" / "SKILL.md", "x").parent
        self.kit.run("--init", "terminal")
        memory = self.kit.memory().splitlines()
        self.assertEqual(memory[:2], ["# мои правила", "@/elsewhere/USER-CLAUDE.md"])
        self.assertEqual(memory[2], "конец")
        settings = self.kit.settings()
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(settings["hooks"]["Stop"][0]["hooks"][0]["command"], "/usr/local/bin/foreign.sh")
        self.assertEqual(len(settings["hooks"]["Stop"]), 2)
        self.assertTrue(foreign_skill.is_dir() and not foreign_skill.is_symlink())

    def node_hook_kit(self, with_python):
        """Хук слоя на node; with_python — рядом лежит прежний <имя>.py."""
        write(self.kit.root / "hooks" / "guard.mjs", "")
        if with_python:
            write(self.kit.root / "hooks" / "guard.py", "")
        write(self.kit.root / "hooks" / "hooks.json", json.dumps({"hooks": {"PreCompact": [{"hooks": [
            {"type": "command", "command": 'node "${CLAUDE_PLUGIN_ROOT}/hooks/guard.mjs" rewake',
             "timeout": 30}]}]}}))

    @unittest.skipIf(sys.platform == "win32", "поддельный node — shell-скрипт")
    def test_node_hook_gets_node_from_path(self):
        self.node_hook_kit(with_python=True)
        fake = write(Path(self.tmp) / "fakebin" / "node", "#!/bin/sh\n")
        fake.chmod(0o755)
        self.kit.env["PATH"] = str(fake.parent)
        self.kit.run("--init", "desktop")
        hook = self.kit.settings()["hooks"]["PreCompact"][0]["hooks"][0]
        self.assertEqual(hook["command"], str(fake))
        self.assertEqual(hook["args"], [(self.kit.root / "hooks" / "guard.mjs").as_posix(), "rewake"])
        self.assertEqual(hook["timeout"], 30)

    def test_no_node_falls_back_to_python_sibling(self):
        self.node_hook_kit(with_python=True)
        self.kit.env["PATH"] = str(Path(self.tmp) / "empty")
        out = self.kit.run("--init", "desktop")
        self.assertIn("Нет node — хук «guard» подключён на Python", out)
        hook = self.kit.settings()["hooks"]["PreCompact"][0]["hooks"][0]
        self.assertEqual(hook["command"], sys.executable)
        self.assertEqual(hook["args"], [(self.kit.root / "hooks" / "guard.py").as_posix(), "rewake"])

    def test_no_node_and_no_python_sibling_skips(self):
        self.node_hook_kit(with_python=False)
        self.kit.env["PATH"] = str(Path(self.tmp) / "empty")
        out = self.kit.run("--init", "desktop")
        self.assertIn("Пропущен хук PreCompact «guard»: нет node", out)
        self.assertNotIn("PreCompact", self.kit.settings().get("hooks", {}))

    def test_switch_layers_removes_stale(self):
        self.kit.run("--init", "terminal")
        self.kit.run("--init", "desktop")
        skills = sorted(p.name for p in (self.kit.claude / "skills").iterdir())
        self.assertEqual(skills, ["dev-method", "night-work"])
        self.assertFalse(os.path.lexists(self.kit.claude / "bin" / "claude-close"))
        self.assertFalse(os.path.lexists(self.kit.claude / "commands" / "restart-cmd.md"))
        self.assertNotIn("SessionStart", self.kit.settings()["hooks"])
        self.assertNotIn("terminal/USER-CLAUDE.md", self.kit.memory())

    def test_exclude(self):
        self.kit.run("--init", "terminal")
        config = self.kit.claude / "claude-kit.json"
        data = json.loads(config.read_text("utf-8"))
        data["exclude"] = ["msk_time", "dev-method", "restart-cmd", "state", "guard"]
        data["clock"] = {"label": "МСК"}
        config.write_text(json.dumps(data), "utf-8")
        self.kit.run()
        self.assertNotIn("hooks", self.kit.settings())
        self.assertFalse(os.path.lexists(self.kit.claude / "skills" / "dev-method"))
        self.assertFalse(os.path.lexists(self.kit.claude / "commands" / "restart-cmd.md"))
        self.kit.run("--init", "terminal")  # повторный --init сохраняет exclude
        self.assertEqual(json.loads(config.read_text("utf-8"))["exclude"],
                         ["msk_time", "dev-method", "restart-cmd", "state", "guard"])
        self.assertEqual(json.loads(config.read_text("utf-8"))["clock"], {"label": "МСК"})

    @unittest.skipIf(sys.platform == "win32", "слой terminal (bin, commands, bash) и symlink — не для Windows")
    def test_old_whole_folder_link_replaced(self):
        # Прежняя домашняя схема: ~/.claude/skills — одна ссылка на skills/ набора,
        # импорт USER-CLAUDE.md уже есть, хук стоял через прежний установщик.
        self.kit.claude.mkdir()
        os.symlink(self.kit.root / "skills", self.kit.claude / "skills")
        import_line = f"@{(self.kit.root / 'USER-CLAUDE.md').as_posix()}"
        write(self.kit.claude / "CLAUDE.md", f"раньше\n{import_line}\nпосле\n")
        old = {"type": "command", "command": sys.executable,
               "args": [(self.kit.root / "hooks" / "msk_time.py").as_posix(), "stop"], "timeout": 10}
        write(self.kit.claude / "settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [old]}]}}))
        self.kit.run("--init", "desktop")
        skills = self.kit.claude / "skills"
        self.assertFalse(skills.is_symlink())
        self.assertEqual(sorted(p.name for p in skills.iterdir()), ["dev-method", "night-work"])
        self.assertEqual(self.kit.memory().splitlines(), ["раньше", import_line, "после"])
        self.assertEqual(self.kit.settings()["hooks"]["Stop"], [{"hooks": [old]}])

    @unittest.skipUnless(sys.platform == "win32", "junction — только Windows")
    def test_old_whole_folder_junction_replaced(self):
        # Дом, 2026-10-03: os.readlink отдаёт цель junction как \\?\D:\..., и прежний
        # junction ~/.claude/skills не узнавался как ссылка набора.
        import _winapi
        self.kit.claude.mkdir()
        _winapi.CreateJunction(str(self.kit.root / "skills"), str(self.kit.claude / "skills"))
        # Во временном каталоге realpath сам снимает префикс, а на D:\dev — нет; проверяем read_link.
        sys.path.insert(0, str(SOURCE.parent))
        try:
            import link
        finally:
            sys.path.pop(0)
        self.assertEqual(link.read_link(self.kit.claude / "skills"), str(self.kit.root / "skills"))
        self.kit.run("--init", "desktop")
        skills = self.kit.claude / "skills"
        self.assertEqual(sorted(p.name for p in skills.iterdir()), ["dev-method", "night-work"])
        self.assertTrue((self.kit.root / "skills" / "dev-method" / "SKILL.md").exists())

    def test_external_layer(self):
        ext = Path(self.tmp).resolve() / "personal"
        write(ext / "USER-CLAUDE.md", "личное\n")
        write(ext / "skills" / "my-game" / "SKILL.md", "x")
        self.kit.run("--init", f"desktop,{ext.as_posix()}")
        skill = self.kit.claude / "skills" / "my-game"
        self.assertEqual(Path(os.path.realpath(skill)), ext / "skills" / "my-game")
        self.assertIn(f"@{ext.as_posix()}/USER-CLAUDE.md", self.kit.memory())
        self.assertIn("Без изменений", self.kit.run())
        # Слой убран из конфига — его ссылки и импорт тоже уходят (корни берутся и из прежнего конфига).
        self.kit.run("--init", "desktop")
        self.assertFalse(os.path.lexists(skill))
        self.assertNotIn(ext.as_posix(), self.kit.memory())

    def test_external_layer_missing(self):
        out = subprocess.run([sys.executable, str(self.kit.root / "scripts" / "link.py"), "--init",
                              (Path(self.tmp) / "nope").resolve().as_posix()],
                             env=self.kit.env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("нет на диске", out.stderr)
        self.assertFalse((self.kit.claude / "claude-kit.json").exists())

    def test_check_changes_nothing(self):
        out = self.kit.run("--check", "--init", "terminal")
        self.assertIn("[проверка]", out)
        self.assertFalse(self.kit.claude.exists())

    @unittest.skipIf(sys.platform == "win32", "слой terminal (bin, commands, bash) и symlink — не для Windows")
    def test_uninstall(self):
        self.kit.run("--init", "terminal")
        self.kit.run("--uninstall")
        self.assertEqual(list((self.kit.claude / "skills").iterdir()), [])
        self.assertEqual(list((self.kit.claude / "commands").iterdir()), [])
        self.assertNotIn("hooks", self.kit.settings())
        self.assertNotIn("env", self.kit.settings())
        self.assertNotIn("USER-CLAUDE.md", self.kit.memory())

    # --- моды: env.CLAUDE_CODE_PLUGIN_DIRS ---

    def test_init_writes_mod_dirs_with_pathsep(self):
        self.kit.run("--init", "terminal")
        value = self.kit.settings()["env"]["CLAUDE_CODE_PLUGIN_DIRS"]
        self.assertEqual(value, os.pathsep.join(m.as_posix() for m in self.kit.mods))
        self.assertEqual(self.kit.mod_dirs(), [m.as_posix() for m in self.kit.mods])
        self.kit.run("--init", "desktop")  # у слоя desktop своих модов нет — остаётся общий
        self.assertEqual(self.kit.mod_dirs(), [self.kit.mods[0].as_posix()])

    def test_mod_needs_plugin_json(self):
        write(self.kit.root / "terminal" / "mods" / "draft" / "hooks" / "register.ts", "")
        self.kit.run("--init", "terminal")
        self.assertEqual(self.kit.mod_dirs(), [m.as_posix() for m in self.kit.mods])

    def test_foreign_mod_dirs_and_env_untouched(self):
        write(self.kit.claude / "settings.json", json.dumps({
            "env": {"HTTP_PROXY": "http://proxy:1", "CLAUDE_CODE_PLUGIN_DIRS": "/elsewhere/mod"}, "model": "opus"}))
        self.kit.run("--init", "terminal")
        settings = self.kit.settings()
        self.assertEqual(settings["env"]["HTTP_PROXY"], "http://proxy:1")
        self.assertEqual(settings["model"], "opus")
        self.assertEqual(self.kit.mod_dirs(), ["/elsewhere/mod"] + [m.as_posix() for m in self.kit.mods])
        self.kit.run("--uninstall")
        self.assertEqual(self.kit.settings()["env"],
                         {"HTTP_PROXY": "http://proxy:1", "CLAUDE_CODE_PLUGIN_DIRS": "/elsewhere/mod"})

    def test_manual_home_entry_replaced_in_place(self):
        # Mac: мод подключён вручную записью с ~; на тот же каталог — без дубля, чужие записи по краям на месте.
        manual = "~/kit/terminal/mods/agent-view-status"
        write(self.kit.claude / "settings.json", json.dumps({"env": {
            "CLAUDE_CODE_PLUGIN_DIRS": os.pathsep.join(["/before", manual, "/after"])}}))
        out = self.kit.run("--init", "terminal")
        self.assertIn(f"Убран каталог мода: {manual}", out)
        self.assertEqual(self.kit.mod_dirs(), ["/before"] + [m.as_posix() for m in self.kit.mods] + ["/after"])
        self.assertIn("Без изменений", self.kit.run())

    def test_relative_entry_is_foreign(self):
        # Относительная запись привязана к cwd: даже из каталога набора она не «наша».
        write(self.kit.claude / "settings.json", json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": "mods/session-state"}}))
        self.kit.run("--init", "terminal", cwd=self.kit.root)
        self.assertEqual(self.kit.mod_dirs(), ["mods/session-state"] + [m.as_posix() for m in self.kit.mods])

    def test_non_string_value_treated_as_empty(self):
        write(self.kit.claude / "settings.json", json.dumps({"env": {"CLAUDE_CODE_PLUGIN_DIRS": ["x"]}}))
        self.kit.run("--init", "terminal")
        self.assertEqual(self.kit.mod_dirs(), [m.as_posix() for m in self.kit.mods])

    def test_exclude_mod(self):
        self.kit.run("--init", "terminal")
        self.kit.set_config(exclude=["session-state"])
        self.kit.run()
        self.assertEqual(self.kit.mod_dirs(), [self.kit.mods[1].as_posix()])
        self.kit.set_config(exclude=["session-state", "agent-view-status"])
        self.kit.run()
        self.assertNotIn("env", self.kit.settings())  # пустой env убран вместе с ключом

    def test_mod_in_two_layers_is_error(self):
        write(self.kit.root / "desktop" / "mods" / "session-state" / ".claude-plugin" / "plugin.json", "{}")
        out = subprocess.run([sys.executable, str(self.kit.root / "scripts" / "link.py"), "--init", "desktop"],
                             env=self.kit.env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("Мод «session-state» есть в двух слоях", out.stderr)

    def test_check_shows_mods_and_writes_nothing(self):
        original = json.dumps({"model": "opus"})
        write(self.kit.claude / "settings.json", original)
        out = self.kit.run("--check", "--init", "terminal")
        self.assertIn(f"[проверка] Каталог мода: +{self.kit.mods[0].as_posix()}", out)
        self.assertEqual((self.kit.claude / "settings.json").read_text("utf-8"), original)
        self.assertFalse((self.kit.claude / "settings.json.bak-claude-kit").exists())
        self.assertFalse((self.kit.claude / "claude-kit.json").exists())

    def test_one_backup_when_hooks_and_env_change(self):
        original = json.dumps({"model": "opus"})
        write(self.kit.claude / "settings.json", original)
        self.kit.run("--init", "terminal")
        settings = self.kit.settings()
        self.assertIn("hooks", settings)
        self.assertIn("env", settings)
        self.assertEqual((self.kit.claude / "settings.json.bak-claude-kit").read_text("utf-8"), original)

    def test_only_env_changes_still_saved(self):
        self.kit.run("--init", "terminal")
        write(self.kit.root / "terminal" / "mods" / "third" / ".claude-plugin" / "plugin.json", "{}")
        before = self.kit.settings()
        self.kit.run()
        after = self.kit.settings()
        self.assertEqual({k: v for k, v in after.items() if k != "env"}, {k: v for k, v in before.items() if k != "env"})
        self.assertEqual(len(self.kit.mod_dirs()), 3)

    def test_separator_is_os_pathsep(self):
        # Windows: «;». На любой ОС подменяем pathsep: запись с «C:/…» не должна ломаться по «:».
        spec = importlib.util.spec_from_file_location("link_under_test", self.kit.root / "scripts" / "link.py")
        link = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(link)
        data = {"env": {"CLAUDE_CODE_PLUGIN_DIRS": "C:/dev/other;/elsewhere"}}
        with mock.patch.object(os, "pathsep", ";"), contextlib.redirect_stdout(io.StringIO()):
            changed = link.sync_env(data, link.wanted_mods([self.kit.root, self.kit.root / "terminal"], set()))
        self.assertTrue(changed)
        self.assertEqual(data["env"]["CLAUDE_CODE_PLUGIN_DIRS"],
                         ";".join(["C:/dev/other", "/elsewhere"] + [m.as_posix() for m in self.kit.mods]))

    # --- мёртвые имена в exclude ---

    def test_dead_exclude_warns_but_changes_nothing(self):
        self.kit.run("--init", "terminal")
        self.kit.set_config(exclude=["knowledge_guard"])
        config_before = (self.kit.claude / "claude-kit.json").read_text("utf-8")
        out = self.kit.run()
        self.assertIn("ВНИМАНИЕ: exclude «knowledge_guard» — такого в слоях нет", out)
        self.assertIn("Без изменений", out)  # предупреждение — не изменение
        self.assertEqual((self.kit.claude / "claude-kit.json").read_text("utf-8"), config_before)
        out = self.kit.run("--check")
        self.assertIn("ВНИМАНИЕ: exclude «knowledge_guard»", out)
        self.assertNotIn("[проверка] ВНИМАНИЕ", out)

    def test_exclude_of_inactive_layer_name_is_silent(self):
        self.kit.run("--init", "terminal")
        # night-work — навык слоя desktop, который на этой машине не включён.
        self.kit.set_config(exclude=["night-work", "session-state", "msk_time", "restart-cmd", "claude-close",
                                     "dev-method", "state"])
        self.assertNotIn("ВНИМАНИЕ", self.kit.run())

    def test_exclude_of_inactive_layer_hook_and_mod_is_silent(self):
        write(self.kit.root / "desktop" / "hooks" / "stop_guard.py", "")
        write(self.kit.root / "desktop" / "hooks" / "hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/stop_guard.py"'}]}]}}))
        write(self.kit.root / "desktop" / "mods" / "desk-mod" / ".claude-plugin" / "plugin.json", "{}")
        self.kit.run("--init", "terminal")
        self.kit.set_config(exclude=["stop_guard", "desk-mod"])
        self.assertNotIn("ВНИМАНИЕ", self.kit.run())

    def test_exclude_of_external_layer_name_is_silent(self):
        ext = Path(self.tmp).resolve() / "personal"
        write(ext / "skills" / "my-game" / "SKILL.md", "x")
        write(ext / "mods" / "my-mod" / ".claude-plugin" / "plugin.json", "{}")
        self.kit.run("--init", f"terminal,{ext.as_posix()}")
        self.kit.set_config(exclude=["my-game", "my-mod", "nowhere"])
        out = self.kit.run()
        self.assertIn("«nowhere»", out)
        self.assertNotIn("«my-game»", out)
        self.assertNotIn("«my-mod»", out)

    def test_broken_hooks_json_of_inactive_layer_does_not_break_run(self):
        write(self.kit.root / "desktop" / "hooks" / "hooks.json", "{ не json")
        self.kit.run("--init", "terminal")
        self.kit.set_config(exclude=["nowhere"])
        self.assertIn("«nowhere»", self.kit.run())

    def test_broken_settings_json_explains(self):
        write(self.kit.claude / "settings.json", "{ не json")
        out = subprocess.run([sys.executable, str(self.kit.root / "scripts" / "link.py"), "--init", "terminal"],
                             env=self.kit.env, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("settings.json", out.stderr)
        self.assertEqual((self.kit.claude / "settings.json").read_text("utf-8"), "{ не json")
        self.assertFalse((self.kit.claude / "skills").exists())  # отказ — до первой правки
        self.assertFalse((self.kit.claude / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
