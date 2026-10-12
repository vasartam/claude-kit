# Claude Code: хуки и моды

Проверено на CLI 2.1.280–2.1.288 (25.09–03.10.2026) на хуках набора (`hooks/README.md`).
Подключает `scripts/link.py`: формат плагина `hooks/hooks.json` → exec-форма в `~/.claude/settings.json`.

## Что куда приходит

- **`UserPromptSubmit`**: `prompt`, `session_id`, `transcript_path`, `permission_mode` — модели и
  effort нет. Его текст идёт с каждым промптом и копится в контексте до сжатия — держать
  коротким (`claude-code-context.md`).
- **`PreToolUse`**: `tool_name`, `tool_input`, `tool_use_id`, `effort`, `permission_mode`.
  `permissionDecision: deny` с причиной — модель видит причину и действует по ней.
- **Effort** приходит в `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop` (`effort.level`,
  переменная `$CLAUDE_EFFORT`), в `UserPromptSubmit` — нет. Модель, лимиты подписки и ultracode
  в хуки не приходят никогда: модель и лимиты знают статусная строка (терминал) и моды, ultracode —
  только транскрипт (`claude-code-context.md`); набору их собирает `hooks/session_state.py`.
- **`Stop`** получает `last_assistant_message`; в транскрипт последний ответ к этому моменту
  может ещё не попасть.
- **`StopFailure`** — ход оборвался ошибкой API: вход `error`, `error_details`,
  `last_assistant_message`; matcher — по `error` из `authentication_failed|oauth_org_not_allowed|
  account_on_hold|verification_required|billing_error|rate_limit|overloaded|invalid_request|
  model_not_found|server_error|unknown|max_output_tokens|cloud_credential_error`. Хук
  **неблокирующий**: вывод игнорируется, продолжить ход он не может.
- «API Error: Connection lost mid-response» — обрыв посреди стрима (`server_error`). Встроенные
  повторы (`CLAUDE_CODE_MAX_RETRIES`, `API_TIMEOUT_MS`) работают только до начала ответа; после
  обрыва ход кончается, и сессия стоит (2.1.282). Продолжить можно только снаружи: в терминале —
  хук `terminal/hooks/api-error-autocontinue.sh` (через herdr), в приложении — его
  автопродолжения после сна и закрытия (`desktop/knowledge/claude-desktop-restart.md`).

## Сжатие и пробуждение

- **`PreCompact` может отменить сжатие**: `decision: block` (проверено 29.09.2026) или выход 2
  (по документации). При ручном `/compact` stderr видит только владелец, модель — нет.
  Отменённое автосжатие повторяется примерно через 30 с, на следующем запросе, пока идёт ход.
  Типы хука — только `command`/`http`/`mcp_tool`.
- **Транскрипт дописывается с задержкой**: промпт, отправленный сразу после сжатия (`claude-compact`,
  мод `context-compact`), приходит через ~0,1 с, и хук `UserPromptSubmit` ещё не видит
  `compact_boundary` — последний ответ в файле тот, что до сжатия (CLI 2.1.288, 03.10.2026).
  Отметку сжатия хуку ставить самому на `SessionStart` (`hooks/README.md`, `note_compact`).
- **Разбудить простаивающую сессию из хука** — обработчик с `asyncRewake: true` и выходом 2: его
  stderr приходит модели напоминанием и запускает ход. Только внутри живого процесса сессии: в
  приложении `SessionStart` с `asyncRewake` модель не будит (`desktop/knowledge/`).
- **Самовозобновление без внешнего координатора** — разовое `CronCreate` (cron с датой,
  `recurring: false`): будит простаивающую сессию в назначенное время. Задания живут в
  процессе сессии: в CLI переживают `claude --resume`, в приложении теряются при его перезапуске.
- Классификатор auto mode блокирует вызов скриптов, меняющих состояние собственных хуков, как
  «Self-Modification». Отсрочку или отметку для хука делать пометкой, которую хук видит сам
  (как `echo knowledge-sorted` у `context-guard`), а не командой, правящей его состояние.

## Запись и подхват

- Правки хуков в `~/.claude/settings.json` идущие сессии подхватывают сами, за секунды (хуки
  плагина — `claude-code-plugins.md`).
- **Exec-форма** (`command` — путь к интерпретатору, `args` — скрипт и аргументы) идёт без
  оболочки и кавычек. JSON в stdout — `ensure_ascii`, текст в stderr — байтами UTF-8 (иначе на
  Windows кодировка консоли бьёт кириллицу).
- Хук-напоминание о документации: `PostToolUse`, `matcher: "Write|Edit"`, `additionalContext`
  «документация — в этом же изменении, куда — таблица в AGENTS.md». Не требовать писать всё в
  один файл: так `CLAUDE.md` одного проекта дорос до 43 КБ и смешал правила с историей.
- Markdown ответа без цвета: цитата `> …` (серая полоска, курсив) — самый неприметный вид
  служебной пометки.

## Моды

Моды (function hooks на TypeScript) — `claude-code-mods.md`.
