"""Подключить claude-kit на этой машине: правила, навыки, хуки и утилиты выбранных слоёв.

python scripts/link.py                         — подключить по конфигу машины
python scripts/link.py --init desktop,windows  — записать конфиг машины и подключить
python scripts/link.py --check                 — только показать, что изменится
python scripts/link.py --uninstall             — убрать всё, что ставил набор

Слой — каталог набора: корень — общий слой (всегда), terminal/, desktop/, macos/, windows/ —
по выбору машины. Внешний слой — абсолютный путь к каталогу той же структуры вне набора (свой
репозиторий, например личный, который не публикуется): --init desktop,windows,D:/dev/<репозиторий>.
В слое могут быть:
  USER-CLAUDE.md   правила на каждую сессию — строка импорта в ~/.claude/CLAUDE.md;
  skills/<имя>/    навыки — ссылка ~/.claude/skills/<имя> (symlink, на Windows junction);
  hooks/hooks.json хуки в формате плагина Claude Code (${CLAUDE_PLUGIN_ROOT} — каталог слоя) —
                   записи в ~/.claude/settings.json: exec-форма, абсолютные пути, python3 — тот
                   же python, что запустил link.py; на Windows хуки на bash пропускаются;
  mods/<имя>/      моды (function hooks; нужен .claude-plugin/plugin.json) — каталог в
                   env.CLAUDE_CODE_PLUGIN_DIRS файла settings.json, разделитель — как у PATH ОС;
  bin/<файл>       утилиты — ссылка ~/.claude/bin/<файл> (кроме Windows);
  commands/<имя>.md  команды — ссылка ~/.claude/commands/<имя>.md (кроме Windows).

Конфиг машины — ~/.claude/claude-kit.json, не в git:
  {"layers": ["terminal", "macos"], "exclude": ["context-guard"]}
exclude — имена навыков, утилит, команд (без .md), хуков (имя скрипта без расширения) и модов
(имя каталога), которые этой машине не нужны. Имя, которого нет ни в одном слое, — мёртвая
запись: link.py предупреждает, конфиг не правит.

Своё скрипт узнаёт по пути внутрь набора или внешнего слоя из конфига: чужие навыки, хуки, каталоги
в CLAUDE_CODE_PLUGIN_DIRS и строки CLAUDE.md не трогает, а ссылки и записи набора, которых больше
нет в выбранных слоях, убирает. Идемпотентно.
~/.claude можно переопределить переменной CLAUDE_CONFIG_DIR (как и у самого Claude Code).
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
ROOTS = [KIT]  # набор и внешние слои: ссылки и записи внутрь них — свои
LAYERS = ("terminal", "desktop", "macos", "windows")
WINDOWS = os.name == "nt"
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
CONFIG = CLAUDE / "claude-kit.json"
CHECK = "--check" in sys.argv[1:]
changes = []


def note(text):
    changes.append(text)
    print(("[проверка] " if CHECK else "") + text)


def posix(path):
    return Path(path).as_posix()


def real(path):
    return Path(os.path.realpath(path))


def inside_kit(path):
    for root in ROOTS:
        try:
            real(path).relative_to(root)
            return True
        except ValueError:
            pass
    return False


def external(name):
    return os.path.isabs(name)


def read_link(path):
    """Цель symlink или junction; None — не ссылка (или её нет)."""
    try:
        target = os.readlink(path)
    except (OSError, ValueError):
        return None
    # Junction на Windows читается как \\?\D:\...; с этим префиксом realpath не сравнить с KIT.
    return target[4:] if target.startswith("\\\\?\\") else target


def make_link(link, target):
    if CHECK:
        return
    if not WINDOWS:
        os.symlink(target, link, target_is_directory=Path(target).is_dir())
        return
    # Junction: без прав администратора и без Developer Mode. Для файла junction невозможен.
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError):
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                       stdout=subprocess.DEVNULL)


def remove_link(link):
    if CHECK:
        return
    # Junction и symlink на каталог в Windows удаляются rmdir, цель не трогается.
    if WINDOWS and Path(link).is_dir():
        os.rmdir(link)
    else:
        os.unlink(link)


def load_config(init):
    if init is not None:
        layers = [x.strip() for x in init.split(",") if x.strip()]
        unknown = [x for x in layers if x not in LAYERS and not external(x)]
        if unknown:
            sys.exit(f"Неизвестные слои: {', '.join(unknown)}. Есть: {', '.join(LAYERS)} или абсолютный путь")
        missing = [x for x in layers if external(x) and not Path(x).is_dir()]
        if missing:
            sys.exit(f"Внешнего слоя нет на диске: {', '.join(missing)}")
        old = json.loads(CONFIG.read_text("utf-8")) if CONFIG.exists() else {}
        # Прочие ключи (clock и др.) — настройки машины, их --init не трогает.
        config = {**old, "layers": layers, "exclude": old.get("exclude", [])}
        if not CHECK:
            CLAUDE.mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
        note(f"Конфиг машины {CONFIG}: слои {', '.join(layers) or 'только общий'}")
        return config
    if not CONFIG.exists():
        sys.exit(f"Нет {CONFIG}. Первый запуск: python {posix(Path(__file__))} --init <слои через запятую>"
                 f" (есть: {', '.join(LAYERS)}; общий слой подключается всегда)")
    return json.loads(CONFIG.read_text("utf-8"))


def layer_dirs(config):
    dirs = [KIT]
    for name in config.get("layers", []):
        if external(name):
            if not Path(name).is_dir():
                sys.exit(f"Внешнего слоя «{name}» из {CONFIG} нет на диске: склонировать или убрать из конфига")
            dirs.append(real(name))
        elif name in LAYERS:
            dirs.append(KIT / name)
        else:
            sys.exit(f"В {CONFIG} неизвестный слой «{name}». Есть: {', '.join(LAYERS)} или абсолютный путь")
    return dirs


def add_roots(config):
    """Внешние слои конфига — тоже «свои», чтобы при смене слоёв и --uninstall убрать их ссылки."""
    for name in config.get("layers", []):
        if external(name) and real(name) not in ROOTS:
            ROOTS.append(real(name))


# --- навыки, утилиты и команды: ссылки в ~/.claude/skills, bin и commands ---

def sync_links(dest, wanted):
    """dest/<имя> → цель для каждого wanted; лишние ссылки набора в dest убрать."""
    if read_link(dest) is not None and inside_kit(os.path.join(os.path.dirname(dest), read_link(dest))):
        # Прежняя схема: вся папка — одна ссылка на skills/ набора. Заменяем папкой со ссылками.
        note(f"{dest}: ссылка целиком на набор заменена папкой с отдельными ссылками")
        remove_link(dest)
    if not CHECK:
        dest.mkdir(parents=True, exist_ok=True)
    present = sorted(dest.iterdir()) if dest.is_dir() else []
    for entry in present:
        target = read_link(entry)
        if target is None or not inside_kit(os.path.join(dest, target)):
            continue
        if entry.name not in wanted or real(os.path.join(dest, target)) != wanted[entry.name]:
            note(f"Убрана ссылка {entry} (было → {target})")
            remove_link(entry)
    for name, target in sorted(wanted.items()):
        link = dest / name
        current = read_link(link)
        if current is not None and real(os.path.join(dest, current)) == target:
            continue
        if current is not None and inside_kit(os.path.join(dest, current)) and CHECK:
            pass  # в режиме проверки убранная выше ссылка ещё на месте
        elif os.path.lexists(link):
            print(f"ВНИМАНИЕ: {link} уже есть и не из набора (→ {current or 'обычный файл/папка'}). "
                  f"Сравнить с {target}, убрать в резервную копию и запустить снова.")
            continue
        make_link(link, target)
        note(f"Ссылка {link} → {target}")


def wanted_skills(dirs, exclude):
    wanted = {}
    for layer in dirs:
        for skill in sorted((layer / "skills").glob("*/SKILL.md")):
            name = skill.parent.name
            if name in exclude:
                continue
            if name in wanted:
                sys.exit(f"Навык «{name}» есть в двух слоях: {wanted[name]} и {skill.parent}")
            wanted[name] = skill.parent
    return wanted


def wanted_bin(dirs, exclude):
    wanted = {}
    if WINDOWS:
        return wanted
    for layer in dirs:
        bindir = layer / "bin"
        for tool in sorted(bindir.iterdir()) if bindir.is_dir() else []:
            if tool.is_file() and os.access(tool, os.X_OK) and tool.name not in exclude:
                if tool.name in wanted:
                    sys.exit(f"Утилита «{tool.name}» есть в двух слоях: {wanted[tool.name]} и {tool}")
                wanted[tool.name] = tool
    return wanted


def wanted_commands(dirs, exclude):
    wanted = {}
    if WINDOWS:
        return wanted
    for layer in dirs:
        for command in sorted((layer / "commands").glob("*.md")):
            if command.stem in exclude:
                continue
            if command.name in wanted:
                sys.exit(f"Команда «{command.stem}» есть в двух слоях: {wanted[command.name]} и {command}")
            wanted[command.name] = command
    return wanted


# --- правила: строки импорта в ~/.claude/CLAUDE.md ---

def sync_imports(dirs):
    memory = CLAUDE / "CLAUDE.md"
    text = memory.read_text("utf-8") if memory.exists() else ""
    lines = text.splitlines()
    wanted = [f"@{posix(d / 'USER-CLAUDE.md')}" for d in dirs if (d / "USER-CLAUDE.md").exists()]

    def ours(line):
        line = line.strip()
        return line.startswith("@") and line.endswith("USER-CLAUDE.md") and inside_kit(line[1:])

    kept = [line for line in lines if not ours(line)]
    first = next((i for i, line in enumerate(lines) if ours(line)), None)
    # Блок импортов набора — на месте первой прежней строки, иначе в конец файла.
    at = len(kept) if first is None else sum(1 for line in lines[:first] if not ours(line))
    new_lines = kept[:at] + wanted + kept[at:]
    if new_lines == lines:
        return
    for line in wanted:
        if line not in lines:
            note(f"Импорт в {memory}: {line}")
    for line in lines:
        if ours(line) and line.strip() not in wanted:
            note(f"Убран импорт из {memory}: {line.strip()}")
    if not CHECK:
        memory.parent.mkdir(parents=True, exist_ok=True)
        memory.write_text("\n".join(new_lines) + "\n", "utf-8")


# --- хуки: записи в ~/.claude/settings.json ---

def exec_form(command, layer):
    """Команда из hooks.json слоя → [программа, аргументы…]; None — на этой ОС не запустить."""
    root = posix(layer)
    parts = [p.replace("${CLAUDE_PLUGIN_ROOT}", root) for p in shlex.split(command)]
    if parts[0] in ("python3", "python"):
        parts[0] = sys.executable
    elif parts[0] in ("bash", "sh") or parts[0].endswith(".sh"):
        if WINDOWS:
            return None
        if "/" not in parts[0]:
            parts[0] = "/bin/" + parts[0]
    return parts


def hook_id(command, layer):
    """Имя для exclude: скрипт слоя без расширения (context-guard для …/context-guard.py hook)."""
    root = posix(layer)
    parts = [p.replace("${CLAUDE_PLUGIN_ROOT}", root) for p in shlex.split(command)]
    scripts = [p for p in parts if p.startswith(root + "/")]
    return Path(scripts[0] if scripts else parts[0]).stem


def layer_hooks(layer):
    """[(событие, группа, хук)] из hooks/hooks.json слоя; нет файла — пусто."""
    spec = layer / "hooks" / "hooks.json"
    if not spec.exists():
        return []
    return [(event, group, hook)
            for event, groups in json.loads(spec.read_text("utf-8"))["hooks"].items()
            for group in groups for hook in group["hooks"]]


def wanted_hooks(dirs, exclude):
    """[(событие, matcher или None, запись хука)] из hooks/hooks.json выбранных слоёв."""
    result = []
    for layer in dirs:
        for event, group, hook in layer_hooks(layer):
            name = hook_id(hook["command"], layer)
            if name in exclude:
                continue
            parts = exec_form(hook["command"], layer)
            if parts is None:
                print(f"Пропущен хук {event} «{name}»: bash на Windows не запускается")
                continue
            extra = {k: v for k, v in hook.items() if k not in ("type", "command", "args")}
            entry = {"type": "command", "command": parts[0], "args": parts[1:], **extra}
            result.append((event, group.get("matcher"), entry))
    return result


def ours_hook(handler):
    parts = [str(handler.get("command", ""))] + [str(a) for a in handler.get("args", [])]
    return any(inside_kit(p) for p in parts if os.path.isabs(p))


def by_event(hooks):
    """Порядок важен только внутри события: settings.json хранит хуки по событиям, а не по слоям."""
    result = {}
    for event, matcher, entry in hooks:
        result.setdefault(event, []).append((matcher, entry))
    return result


def load_settings():
    """(данные, исходный текст) из ~/.claude/settings.json; файла нет — ({}, None)."""
    path = CLAUDE / "settings.json"
    if not path.exists():
        return {}, None
    text = path.read_text("utf-8")
    try:
        data = json.loads(text or "{}")
    except ValueError as error:
        sys.exit(f"{path}: не разобрать JSON ({error}). Поправить файл и запустить снова")
    if not isinstance(data, dict):
        sys.exit(f"{path}: верхний уровень должен быть объектом JSON. Поправить файл и запустить снова")
    return data, text


def save_settings(data, text):
    """Одна запись за запуск: бэкап прежнего текста (если файл был) и новый settings.json."""
    if CHECK:
        return
    path = CLAUDE / "settings.json"
    if text is not None:
        path.with_name("settings.json.bak-claude-kit").write_text(text, "utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def sync_hooks(data, dirs, exclude):
    """Хуки набора в data["hooks"]; True — data изменён (запись — на save_settings)."""
    settings_path = CLAUDE / "settings.json"
    hooks = data.get("hooks", {})
    wanted = wanted_hooks(dirs, exclude)
    present = [(event, group.get("matcher"), h)
               for event, groups in hooks.items() for group in groups
               for h in group.get("hooks", []) if ours_hook(h)]
    if by_event(present) == by_event(wanted):
        return False  # уже стоит ровно как надо — чужой порядок не трогаем
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            rest = [h for h in group.get("hooks", []) if not ours_hook(h)]
            if rest:
                groups.append({**group, "hooks": rest})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    for event, matcher, entry in wanted:
        group = {"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}
        hooks.setdefault(event, []).append(group)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    gone = [p for p in present if p not in wanted]
    added = [w for w in wanted if w not in present]
    for event, _, h in gone:
        note(f"Убран хук {event}: {' '.join(map(str, h.get('args', []))) or h.get('command')}")
    for event, _, h in added:
        note(f"Хук {event}: {' '.join(map(str, h['args'])) or h['command']}")
    if not gone and not added:
        note(f"Хуки набора в {settings_path} переставлены в конец списков")
    return True


# --- моды: каталоги в env.CLAUDE_CODE_PLUGIN_DIRS файла settings.json ---

ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"


def wanted_mods(dirs, exclude):
    """{имя: каталог} модов <слой>/mods/<имя>/ с .claude-plugin/plugin.json: по слоям, внутри слоя — по имени."""
    wanted = {}
    for layer in dirs:
        for spec in sorted((layer / "mods").glob("*/.claude-plugin/plugin.json")):
            mod = spec.parent.parent
            if mod.name in exclude:
                continue
            if mod.name in wanted:
                sys.exit(f"Мод «{mod.name}» есть в двух слоях: {wanted[mod.name]} и {mod}")
            wanted[mod.name] = mod
    return wanted


def ours_dir(entry):
    """Запись набора: путь внутрь набора или внешнего слоя; ручная запись с ~ тоже считается."""
    path = os.path.expanduser(entry)
    return os.path.isabs(path) and inside_kit(path)  # относительный путь привязан к cwd — не наш


def sync_env(data, mods):
    """env.CLAUDE_CODE_PLUGIN_DIRS в data: каталоги модов набора, чужие записи на месте; True — data изменён."""
    env = data.get("env")
    env = env if isinstance(env, dict) else {}
    value = env.get(ENV_KEY)
    old = [e for e in value.split(os.pathsep) if e.strip()] if isinstance(value, str) else []
    wanted = [posix(real(path)) for path in mods.values()]
    kept = [e for e in old if not ours_dir(e)]
    first = next((i for i, e in enumerate(old) if ours_dir(e)), None)
    # Блок записей набора — на месте первой прежней, иначе в конец списка.
    at = len(kept) if first is None else sum(1 for e in old[:first] if not ours_dir(e))
    new = kept[:at] + wanted + kept[at:]
    if new == old:
        return False
    added = [path for path in wanted if path not in old]
    gone = [e for e in old if ours_dir(e) and e not in wanted]
    for path in added:
        note(f"Каталог мода: +{path}")
    for entry in gone:
        note(f"Убран каталог мода: {entry}")
    if not added and not gone:
        note(f"Каталоги модов набора в env.{ENV_KEY} переставлены")
    if new:
        env[ENV_KEY] = os.pathsep.join(new)
        data["env"] = env
    else:
        env.pop(ENV_KEY, None)
        if env:
            data["env"] = env
        else:
            data.pop("env", None)
    return True


# --- мёртвые имена в exclude ---

def known_names(dirs):
    """Имена навыков, утилит, команд, хуков и модов общего слоя, всех встроенных и подключённых внешних —
    и тех, что на этой машине не включены: exclude бывает общим для машин."""
    names = set()
    for layer in [KIT, *(KIT / name for name in LAYERS), *dirs]:
        names.update(p.parent.name for p in (layer / "skills").glob("*/SKILL.md"))
        names.update(p.parent.parent.name for p in (layer / "mods").glob("*/.claude-plugin/plugin.json"))
        names.update(p.stem for p in (layer / "commands").glob("*.md"))
        if (layer / "bin").is_dir():
            names.update(p.name for p in (layer / "bin").iterdir() if p.is_file())
        try:
            names.update(hook_id(hook["command"], layer) for _, _, hook in layer_hooks(layer))
        except (OSError, ValueError, LookupError, TypeError):
            pass  # кривой hooks.json неактивного слоя не должен ломать запуск ради предупреждения
    return names


def warn_dead_exclude(dirs, exclude):
    """Имя из exclude, которого нет ни в одном слое, ничего не исключает (хук переименовали или убрали)."""
    known = known_names(dirs)
    for name in sorted(str(x) for x in exclude if x not in known):
        print(f"ВНИМАНИЕ: exclude «{name}» — такого в слоях нет")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")  # sys.exit(...) по-русски; на Windows иначе cp1251
    args = sys.argv[1:]
    if CONFIG.exists():
        add_roots(json.loads(CONFIG.read_text("utf-8")))
    if "--uninstall" in args:
        config, dirs, exclude = {"layers": []}, [], set()
    else:
        init = args[args.index("--init") + 1] if "--init" in args else None
        config = load_config(init)
        add_roots(config)
        dirs, exclude = layer_dirs(config), set(config.get("exclude", []))
    data, text = load_settings()  # битый settings.json — отказ до первой правки
    sync_imports(dirs)
    sync_links(CLAUDE / "skills", wanted_skills(dirs, exclude))
    if not WINDOWS:
        sync_links(CLAUDE / "bin", wanted_bin(dirs, exclude))
        sync_links(CLAUDE / "commands", wanted_commands(dirs, exclude))
    changed = sync_hooks(data, dirs, exclude)
    if sync_env(data, wanted_mods(dirs, exclude)):
        changed = True
    if changed:
        save_settings(data, text)
    warn_dead_exclude(dirs, exclude)
    if not changes:
        print(f"Без изменений: слои {', '.join(['общий'] + list(config.get('layers', [])))} уже подключены")


if __name__ == "__main__":
    main()
