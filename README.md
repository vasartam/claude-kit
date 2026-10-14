# claude-kit — личный набор владельца для Claude Code

Правила, навыки, знания, хуки и утилиты, которые нужны агентам **в любом** проекте владельца
([github.com/vasartam/claude-kit](https://github.com/vasartam/claude-kit)). Правится только здесь, проекты его не
копируют; факты одного проекта — в его `docs/`.

## Как установить

Набор писался под одного человека: правила `USER-CLAUDE.md` обращаются к владельцу по имени, требуют
отвечать по-русски и задают его привычки. Поэтому **целиком** (`scripts/link.py`) — только машины
владельца; остальным — **по частям**: один-два навыка или мод, без правил и хуков.

| Как | Что попадёт в каждую сессию | Кому |
|---|---|---|
| Один навык | строка «имя — описание» в списке навыков; текст `SKILL.md` — только при вызове | всем |
| Мод (`mods/<имя>/`, плагин) | его хуки на каждом шаге | кто прочитал README мода: часть модов работает только в паре с хуками набора |
| Целиком, `link.py` | `USER-CLAUDE.md` общего слоя и выбранных (≈ 4 КБ + 2–3 КБ на слой), все навыки слоёв, строки хуков к каждому промпту | владелец |

### Один навык

Навыки ссылаются на знания набора путём `<kit>/knowledge/…`; без правил набора модель не знает, что
такое `<kit>`, — это одна строка в `~/.claude/CLAUDE.md`. Клон — целиком, навык — ссылкой на него.

```bash
git clone https://github.com/vasartam/claude-kit.git ~/claude-kit
mkdir -p ~/.claude/skills && ln -s ~/claude-kit/skills/dev-method ~/.claude/skills/dev-method
echo '<kit> в навыках claude-kit — это ~/claude-kit.' >> ~/.claude/CLAUDE.md
```

Навык виден со следующей сессии, вызывается `/dev-method`. Не нужен — удалить ссылку
`~/.claude/skills/<имя>`. Обновить — `git -C ~/claude-kit pull`.

Без остального набора работают `dev-method`, `feature-autopilot`, `screen-selfcheck`, `message-draft`
(стиль писем — по-русски), `morning-newspaper`; `rebase-on-master` — со ссылкой на утилиту:
`mkdir -p ~/.claude/bin && ln -s ~/claude-kit/bin/git-rebase-master ~/.claude/bin/`. Навыки сессии (`session-end`, `session-extract-knowledge`) и навыки
слоёв (`terminal/`, `desktop/`) опираются на хуки и утилиты набора — их берите только целиком.

### Мод

```bash
claude --plugin-dir ~/claude-kit/mods/<имя>     # на одну сессию
```

Насовсем — каталог мода в `env.CLAUDE_CODE_PLUGIN_DIRS` файла `~/.claude/settings.json`. Что мод делает
и от чего зависит — его `README.md`.

### Промпт для своего Claude

Вставить в Claude Code, заменив список нужного:

```text
Подключи мне из репозитория https://github.com/vasartam/claude-kit только навыки dev-method и
rebase-on-master. Склонируй его в ~/claude-kit (есть — git pull), прочитай README.md, раздел
«Как установить», и сделай по нему: навыки — ссылками в ~/.claude/skills, утилиты, которые им нужны, —
ссылками в ~/.claude/bin, в ~/.claude/CLAUDE.md — одна строка про <kit>. USER-CLAUDE.md, хуки и моды
не подключай, settings.json не трогай. В конце покажи, что изменил и как это откатить.
```

Целиком, на машине владельца:

```text
Подключи набор ~/claude-kit на этой машине: прочитай README.md, раздел «Подключить на машине»,
выбери слои под мою ОС и оболочку, запусти scripts/link.py --check и покажи план, после моего «да» —
link.py --init <слои>.
```

Подробно о целиком — раздел «Подключить на машине» ниже.

## Слои

Машина подключает общий слой и те слои, что ей подходят. Правило слоя уточняет общее; при
конфликте главнее более частное.

| Слой | Каталог | Что там |
|---|---|---|
| общий | корень | правила каждой сессии, навыки, знания и утилиты для любой машины и оболочки; хуки контроля контекста и лимита, совета по модели и времени МСК; мод состояния сессии для них |
| terminal | `terminal/` | Claude Code в терминале и herdr: переключение модели, сжатие и закрытие сессии, статусная строка, продолжение после ошибки API, перезапуск на месте |
| desktop | `desktop/` | приложение Claude (вкладка Code): модель и effort (запасной путь — `get_session`), общий лимит всех сессий, ночная работа |
| macos | `macos/` | macOS: клавиши, права TCC, iTerm, Keychain, launchd, голосовой ввод |
| windows | `windows/` | Windows 11 + WSL: Git Bash и PowerShell, push из WSL, грабли оболочек |

Linux — потом слоем `linux/`: каталог с той же структурой и слово в `LAYERS` у `scripts/link.py`.

## Что внутри

```
claude-kit/
  USER-CLAUDE.md           жёсткие правила каждой сессии (одна строка на правило) и указатели на знания
  skills/<имя>/SKILL.md    навыки — личные навыки Claude Code
    dev-method/            методология разработки (ядро ≤ ~8 КБ + references/)
    rebase-on-master/      ребейз своей ветки на свежую основную: скрипт, сверка diff, проверки, force-with-lease
    feature-autopilot/     фича от описания до Draft-MR без пошагового участия: фазы, пакет вопросов, границы
    screen-selfcheck/      самопроверка UI до показа человеку: матрица состояний, снимки, цикл «тест → фикс»
    message-draft/         черновик сообщения от лица владельца (без отправки); стиль — knowledge/writing*.md
    morning-newspaper/     «утренняя газета»: сверстанный отчёт-Artifact и шаблон отчёта после работы без владельца
    session-extract-knowledge/  разложить знания по местам (по просьбе, перед сжатием, по зову хука); таблица «куда» по умолчанию
    session-end/           конец сессии: проверка открытого, раскладка, закрытие способом оболочки, слово прощания
  hooks/                   хуки общего слоя: hooks.json; context-guard.mjs (знания — на полочки до сжатия),
                           model-advisor.py (совет по модели), limit-guard.py (пауза у лимита), msk_time.py
                           (время МСК и отметка в конце ответа); session_state.py и session-state.mjs — их
                           источник состояния сессии; тесты test_*.py, test_*.mjs; README.md — что какой делает
  mods/session-state/      мод: контекст, порог автосжатия, лимиты, модель и effort сессии — файлом для хуков
  mods/msk-time/           мод: время в конец ответа дописывает программа, а не модель
  mods/context-compact/    мод: сжатие после раскладки без панели herdr — сигналом модели, как claude-compact
  chrome/claude-groups-cleaner/  расширение Chrome: закрывает брошенные группы вкладок «Claude» (ставится вручную, README)
  bin/                     утилиты (link.py ставит ссылки в ~/.claude/bin, кроме Windows):
                           automode-add-rule — правило в autoMode.allow (запускает владелец);
                           git-rebase-master — ребейз на основную ветку из origin/HEAD (алиас git rbm)
  knowledge/README.md      индекс общих знаний (строка на файл: что внутри, когда читать) и ссылки на индексы слоёв
  knowledge/*.md           одна тема — один файл
  templates/agents-pointer.md  фрагмент для AGENTS.md проекта
  scripts/link.py          подключить набор на машине (ниже); test_link.py — его тесты
  scripts/token-usage.js   расход токенов по сессиям за период (knowledge/subagents.md)
  terminal/                слой «Claude Code в терминале и herdr»: USER-CLAUDE.md (метка вкладки, переключение
                           модели, сжатие и закрытие сессии), hooks/ (api-error-autocontinue; README.md),
                           bin/ (claude-compact, claude-close, claude-restart, claude-statusline и др.),
                           skills/restart-session, commands/restart-cmd.md, mods/agent-view-status,
                           knowledge/ (herdr, хоткеи, статусная строка, утилиты)
  desktop/                 слой «приложение Claude»: USER-CLAUDE.md (модель и effort, запасной путь — get_session;
                           сжатие и закрытие сессии), skills/limit-guard (координатор общего лимита), skills/night-work
                           (ночная работа сессий), knowledge/ (перезапуск и будильники, окружение вкладки Code)
  macos/                   слой macOS: USER-CLAUDE.md (клавиши Option/Cmd, права, HTTPS через curl),
                           knowledge/ (TCC, iTerm, голос, Keychain, launchd, UI-автоматизация и др.)
  windows/                 слой «Windows 11 + WSL»: USER-CLAUDE.md (оболочки, пути с `\`),
                           knowledge/ (shell-pitfalls.md, git-windows.md)
  AGENTS.md, CLAUDE.md     правила для агента, который правит сам набор
  LICENSE                  MIT
```

Слой устроен как корень: `USER-CLAUDE.md`, `skills/`, `hooks/hooks.json`, `bin/`, `commands/`,
`knowledge/` (с индексом `README.md`), `mods/` — что нужно.

## Подключить на машине

Нужны git, Python 3.9+ (на Windows команда — `python`) и Node 18+ в PATH (хук context-guard; без node
link.py пока подключает его Python-версию). Каталог набора дальше — `<kit>`.

```bash
git clone git@github.com:vasartam/claude-kit.git <kit>
python3 <kit>/scripts/link.py --init <слои через запятую>
```

| Машина | Слои | `exclude` |
|---|---|---|
| macOS, Claude Code в терминале и herdr | `terminal,macos` | — |
| Windows 11 + WSL, приложение Claude | `desktop,windows` (Windows-python: PowerShell или Git Bash) | — |
| Claude Code внутри WSL (свой `~/.claude`), если нужен | `""` — только общий слой; `<kit>` там — `/mnt/d/dev/claude-kit` | — |

- `--init` записывает конфиг машины `~/.claude/claude-kit.json` (не в git) и подключает; дальше
  `python3 <kit>/scripts/link.py` без аргументов — по конфигу. Общий слой подключается всегда.
- Прежние `scripts/link-windows.ps1`, `scripts/link-wsl.sh` и `hooks/install.py` заменены `link.py`:
  их ссылку `~/.claude/skills` на всю папку навыков и записи хуков он заменяет сам. В WSL Claude Code
  не нужен — там `python3 /mnt/d/dev/claude-kit/scripts/link.py --uninstall`.
- `--check` — показать, что изменится, ничего не меняя.
- `--uninstall` — убрать всё, что ставил набор; чужие навыки, хуки и строки не трогает.
- Лишнее машине — имя в `"exclude"` конфига (навык, утилита, команда без `.md`, хук — имя скрипта
  без расширения, мод — имя каталога), затем `link.py` ещё раз:
  `{"layers": ["terminal", "macos"], "exclude": ["context-guard"]}`. Имя, которого нет ни в одном
  слое, — мёртвая запись: `link.py` пишет «ВНИМАНИЕ: exclude «…» — такого в слоях нет», конфиг не правит.

Что ставит `link.py` (идемпотентно, своё узнаёт по пути внутрь набора):

| Что | Куда |
|---|---|
| `USER-CLAUDE.md` общего слоя и выбранных | строки `@<kit>/…/USER-CLAUDE.md` в `~/.claude/CLAUDE.md` — грузятся в каждую сессию без окна подтверждения (кроме Cowork) |
| навыки `skills/<имя>/` | ссылка `~/.claude/skills/<имя>` (symlink, на Windows junction) — личный навык: виден во всех проектах и worktree, важнее проектного с тем же именем |
| хуки | записи в `~/.claude/settings.json` (`hooks`) из `hooks/hooks.json` общего слоя и выбранных слоёв (формат плагина, `${CLAUDE_PLUGIN_ROOT}` — каталог слоя); лишний машине хук — его имя в `exclude`; правка скрипта действует сразу (подробно — `hooks/README.md`, `terminal/hooks/README.md`) |
| моды `<слой>/mods/<имя>/` (с `.claude-plugin/plugin.json`) | каталог в `env.CLAUDE_CODE_PLUGIN_DIRS` файла `~/.claude/settings.json` (разделитель — как у PATH ОС); свои записи, в том числе ручные с `~`, заменяет, чужие не трогает; мод грузится при старте сессии, правки подхватывает на лету |
| утилиты `bin/*` | ссылка `~/.claude/bin/<имя>` (кроме Windows) |
| команды `commands/*.md` | ссылка `~/.claude/commands/<имя>.md` (кроме Windows) |
| знания | не грузятся сами: правила указывают на индекс `knowledge/README.md`, агент открывает только нужный файл |

Руками, один раз:

- Чтобы агент из любого проекта правил набор без запроса доступа — в `~/.claude/settings.json`
  ключ `"permissions": {"additionalDirectories": ["<kit>"]}` (владелец вписывает сам).
- Слой terminal: кроме link.py, руками — `statusLine` в `settings.json`, хоткеи herdr, ссылка
  `herdr-namer-claude` в `~/.local/bin`, необязательный
  `~/.claude/claude-restart.local.zsh` — `terminal/knowledge/tools.md`, раздел «Что link.py не делает».
- Codex, Cursor и другие инструменты `~/.claude` не читают — указатель в `AGENTS.md` проекта
  (`templates/agents-pointer.md`). Облачные сессии и routines тоже: нужный навык — загрузить в
  claude.ai (Customize → Skills).

Проверка: `python3 <kit>/scripts/link.py --check` пишет «Без изменений…»; тесты (из `<kit>`) —
`python3 scripts/test_link.py`, `for t in hooks/test_*.py; do python3 "$t"; done` и `node --test hooks/test_*.mjs`, мод —
`mods/session-state/README.md`.
SSH к gitlab.com рвётся — через altssh:443 (`knowledge/git-gitlab.md`); на Windows push — из WSL
(`windows/USER-CLAUDE.md`).

## Обновить

```bash
git -C <kit> pull --rebase && python3 <kit>/scripts/link.py
```

Правка `SKILL.md`, скрипта хука, мода или утилиты действует сразу (это ссылки и пути на файлы набора),
`USER-CLAUDE.md` — со следующей сессии. Новый навык, утилита, команда, правка `hooks.json` и смена
слоёв — после `link.py`; новый мод — после `link.py` и перезапуска сессии (приложения — целиком).
Переезды, заглушки по старым путям и шаги перехода на машинах — `CHANGELOG.md`.

## Как правится

Набор живой: общее правило, знание или навык, найденные в работе над любым проектом, агент вносит
сюда сам, в той же сессии, а в проекте оставляет только частности. Правила правки — `AGENTS.md`.
