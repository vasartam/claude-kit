# Утилиты слоя terminal

Исходники — `<kit>/terminal/bin/`, `scripts/link.py` кладёт ссылки в `~/.claude/bin/<имя>`. Утилиты
зовут друг друга и хуки зовут их как `~/.claude/bin/<имя>`. Работают на macOS и Linux.

**Нужно:** `zsh` (утилиты `claude-*` на zsh; в Debian — `sudo apt install zsh`), `jq` 1.6+, `perl`
(отвязка через `setsid`), `python3` 3.9+, `herdr` в PATH или `~/.local/bin/herdr`. PATH у хоткея
herdr и у статусной строки бывает урезанным — утилиты дописывают в конец `~/.local/bin`,
`/usr/local/bin`, `/opt/homebrew/bin`. Журналы — `~/.config/herdr/<утилита>.log`.

Вызывать полным путём `~/.claude/bin/<имя>`. Утилиты, которые зовут изнутри сессии (`claude-close`,
`claude-compact`, `claude-restart`, `claude-autocontinue`), сами отвязываются (setsid) и
возвращаются сразу, а действие делают после конца хода — после вызова ход заканчивать.

| Утилита | Что делает |
|---|---|
| `claude-statusline` | статусная строка CLI и данные сессии для хуков; в `~/.claude/session-status/rate-limits.json` — самые свежие лимиты из всех сессий, окно с прошедшим сбросом — 0% — [statusline.md](statusline.md) |
| `claude-usage-report` | расход лимитов и контекста по дням (день с 09:00) и сессиям: 7д, пик 5ч, сжатия «до→после», максимальный контекст. `--days N`, `--sessions`, `--day ДД.ММ`. Проценты — из `~/.claude/usage-log/limits.jsonl` (пишет статусная строка); где журнала нет — оценка «~» по токенам транскриптов с весами цены моделей. claude.ai в браузере и другие машины не видны |
| `claude-compact [PANE] [-- ИНСТРУКЦИЯ]` | после конца хода шлёт в панель `/compact ИНСТРУКЦИЯ`, после сжатия — «продолжи по инструкции». `--resend FILE`, `--no-continue`, `--now`. Как пользоваться, длина инструкции, файл после сжатия — [session-compact.md](session-compact.md) |
| `claude-close [PANE] [--now]` | после конца хода гасит claude и закрывает панель herdr (последняя — с вкладкой) — [session-compact.md](session-compact.md) |
| `claude-restart` | перезапуск в той же панели с тем же разговором — [restart-in-place.md](restart-in-place.md), навык `/restart-session`. `[PANE]`, `--focused` (хоткей), `--stale`, `--knowledge`, `--prompt TEXT`, `--now`, `--dry-run` |
| `claude-live-tasks.py <sid>` | по транскрипту: живые фоновые задачи (Bash в фоне, Monitor, Agent) и несработавший будильник `/loop`, JSON `{tasks, wakeup}`; нужен `claude-restart` |
| `claude-prompt TARGET TEXT` | замена `herdr agent prompt` для всех утилит и хуков набора: если владелец набирает промпт и не отправил, ждёт паузы в наборе (2 с), прячет черновик в stash Claude Code (Ctrl+S), шлёт TEXT; черновик Claude Code возвращает сам. Без этого промпт утилиты дописывался в конец черновика и уходил с ним, а слэш-команда не в начале — уже не команда (13.10.2026: недописанный промпт + `/session-extract-knowledge` от context-guard одним сообщением). Пустое поле узнаёт по тусклой подсказке «Try …»; поля на экране нет — шлёт как раньше; отправка сорвалась — возвращает черновик повторным Ctrl+S. Новый отправитель промптов — только через неё |
| `claude-autocontinue <sid> [сек]` | ждёт ответа на совет хука model-advisor (по умолчанию 120 с); агент не начал работать — шлёт «Продолжай…». Помечает сессию `~/.claude/session-status/<sid>.advised`, чтобы ответ на совет не вызвал новый совет |
| `herdr-namer-claude` | шим автоименования вкладок herdr (ниже) |
| `keyprobe.py` | что терминал шлёт при нажатии клавиш — [hotkeys.md](hotkeys.md) |

## Шим herdr-namer-claude

Повторяет интерфейс `codex exec … -o <файл> <промпт>`: плагин herdr-plugin-renamer
([herdr-naming.md](herdr-naming.md)) зовёт его вместо Codex и берёт слаг из файла. Подключение —
ссылка в `~/.local/bin` (там шим по умолчанию ищет пропатченный renamer) и переменные в `~/.zshrc`
(действуют после перезапуска сервера herdr: плагины видят его окружение, [herdr.md](herdr.md)):

```sh
ln -sfn ~/.claude/bin/herdr-namer-claude ~/.local/bin/herdr-namer-claude
export HERDR_NAMING_ENGINE=codex
export HERDR_NAMING_CODEX_BIN="$HOME/.local/bin/herdr-namer-claude"
```

Внутри — `claude -p` на Haiku 4.5 без хуков (`--settings '{"disableAllHooks":true}'`), без MCP и **без
инструментов** (`--tools ""`): в промпт плагина попадает текст вкладки, и без флага claude принимал
его за задание и повторял команды вкладки в фоне (25.09.2026, копии висели сиротами). Лимит 25 с (у
плагина — 30 с), типовой прогон ~10 с. Ответ, не похожий на слаг (`^[a-z0-9]+(-[a-z0-9]+){1,5}$`), —
выход 1, плагин соберёт слаг сам. Переменные: `HERDR_NAMING_CLAUDE_MODEL`, `HERDR_NAMING_CLAUDE_BIN`,
`HERDR_NAMING_CLAUDE_TIMEOUT`.

## Что link.py не делает (вписать руками)

- `statusLine` — [statusline.md](statusline.md); мод `agent-view-status` link.py вписывает сам
  (`env.CLAUDE_CODE_PLUGIN_DIRS`);
- хоткеи herdr (`~/.config/herdr/config.toml`) — [hotkeys.md](hotkeys.md);
- ссылка и переменные шима — выше;
- добавки машины к `claude-restart` — `~/.claude/claude-restart.local.zsh`.
