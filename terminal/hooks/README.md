# terminal/hooks — хуки слоя terminal

Хуки Claude Code в терминале (CLI) под herdr, переносимы между macOS и Linux. Какие события на
какой скрипт — `hooks.json` рядом (формат плагина, `${CLAUDE_PLUGIN_ROOT}` — каталог `terminal/`); в
`~/.claude/settings.json` их вписывает `scripts/link.py`, если в конфиге машины есть слой `terminal`.
Правка скрипта действует сразу; правка `hooks.json` — после повторного `python3 scripts/link.py`.
Лишний машине хук — имя скрипта без расширения в `exclude`.

Зависимость — herdr (`command -v herdr`, запасной путь `~/.local/bin/herdr`; панель — `$HERDR_PANE_ID`).

| Скрипт | События | Что делает |
|---|---|---|
| `api-error-autocontinue.sh` | `StopFailure` (`server_error\|overloaded\|unknown`) | ответ оборвался на ошибке API — «продолжай» агенту в его панель |

`context-guard`, `model-advisor` и `limit-guard` переехали в общий слой и работают в любой оболочке —
`hooks/README.md`; концовка context-guard в панели herdr (`claude-compact --resend`, раскладка агенту
через `herdr agent prompt`) и строка `wait:` model-advisor — там же. Здесь по старым путям — заглушки
на один выпуск (`CHANGELOG.md`). Данные для них в терминале пишет и статусная строка
`terminal/bin/claude-statusline` (`~/.claude/session-status/`).

## api-error-autocontinue

Обрыв ответа посреди стрима ход заканчивает, а `StopFailure` продолжить его не может (почему —
`knowledge/claude-code-hooks.md`). Поэтому хук в фоне ждёт `herdr agent wait <pane> --until idle` и
шлёт `herdr agent prompt` «Продолжай…». Не больше 5 раз за 10 мин на сессию (`API_AUTOCONTINUE_MAX`,
`API_AUTOCONTINUE_WINDOW`), журнал — `$TMPDIR/claude-api-autocontinue/`. Вне herdr сразу выходит.

## Интеграция herdr — не здесь

Отчёт herdr о сессии (`pane.report_agent_session` на `SessionStart`: по нему herdr следит за агентом и
именует вкладку) ставит сам herdr: `herdr integration install claude` кладёт
`~/.claude/hooks/herdr-agent-state.sh` и запись `SessionStart` в `settings.json`, `herdr integration
status` её проверяет. Файл ведёт herdr и перезаписывает при обновлении интеграции, поэтому в наборе
его нет: своя копия в `hooks.json` дала бы второй такой же отчёт на каждый старт и отставала бы от herdr.
