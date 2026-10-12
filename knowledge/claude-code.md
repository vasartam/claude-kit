# Claude Code: обзор и механика

Проверенные факты о Claude Code (CLI и вкладка Code приложения). Темы по файлам:

| Файл | Что внутри |
|---|---|
| `claude-code-instructions.md` | личные навыки, `CLAUDE.md` и `AGENTS.md`, импорты, вложенные `CLAUDE.md`, память и её пределы |
| `claude-code-sessions.md` | effort, workflow и ultracode, версия процесса, облачные сессии, имена сессий; правила имён и тиков лупа |
| `claude-code-plugins.md` | плагины: namespace, кэш установки, `/reload-plugins`, хуки только из `hooks/hooks.json`; суффикс `-cmd` |
| `claude-code-hooks.md` | что приходит в хуки, `PreCompact` и `asyncRewake`, `StopFailure`, `CronCreate`, exec-форма |
| `claude-code-mods.md` | моды: подключение, приложение, сжатие из мода |
| `claude-code-context.md` | заполнение контекста по записи сессии, автосжатие, что есть в транскрипте, сколько стоит контекст |
| `claude-code-permissions.md` | правила `permissions`, классификатор auto mode: что режет, что работает, `autoMode.allow` |
| `claude-code-headless.md` | `claude -p`: вложенный запуск, детерминированный прогон, `~/.claude/**`, учётка |
| `claude-in-chrome.md` | браузер агента: группа вкладок, ввод, фильтры секретов, прямые ссылки |
| `claude-models.md` | лимиты тарифа, выбор модели и effort, кэш и цены |
| `model-advice.md` | совет по модели и effort: строка хука `model-advisor`, лимиты и норма недели |

Только терминал — `terminal/knowledge/`:
- `statusline.md` — статусная строка, мод agent-view-status;
- `hotkeys.md` — хоткеи herdr и Claude Code, русская раскладка, `keybindings.json`;
- `restart-in-place.md` — перезапуск в панели, фоновая сессия (`claude attach`);
- `updates.md` — версия живой панели, нативный установщик, голос после обновления;
- `herdr-naming.md` — метка вкладки, подмена заголовка терминала именем сессии;
- `herdr.md` — herdr; `tools.md` — утилиты.

Только приложение — `desktop/knowledge/`: `claude-desktop.md` — окружение и сеть,
`claude-desktop-restart.md` — перезапуск, сон, будильники, рутины.

## MCP, настройки, окружение

- **Новый MCP-сервер** (`claude mcp add …`) подключается командой `/reload-skills` — не
  советовать перезапуск сессии; потом проверить `ToolSearch` по имени сервера.
- **MCP с OAuth на Authenticate отвечает `invalid_client: unknown client_id`** — в хранилище
  учёток Claude Code осталась старая динамическая регистрация этого сервера. Удалить только его
  запись и заново Authenticate; на macOS — `macos/knowledge/keychain.md`.
- `"ENABLE_TOOL_SEARCH": "true"` в `env` настроек — схемы инструментов грузятся лениво через
  `ToolSearch`, в контексте только имена; окупается при многих MCP-серверах.
- Доступ агента к папке вне проекта без запросов — `permissions.additionalDirectories` в
  `~/.claude/settings.json` (правит владелец).
- Claude Code работает только во время диалога (и задач по расписанию, пока сессия открыта):
  следить за каналом, счетами, сайтом он не может. Постоянная работа — сервис, который сам зовёт
  API Claude; Claude Code его пишет и сопровождает.
- **Инструмент Bash идёт без TTY**: интерактивный конфигуратор падает на первом вопросе
  (`rclone config` → `Failed to read line: EOF`). Давать неинтерактивную форму
  (`rclone config create … --obscure`, `gh auth login --with-token`, `git config …`); нет её —
  владелец запускает в отдельном терминале, не в окне Claude (набранное там уходит в контекст),
  с ведущим пробелом, чтобы строка не осела в истории. Результат проверять самому, без секретов
  в выводе.
- На macOS диалог «<терминал> запрашивает доступ к данным других приложений» поднимает сам
  `claude`: на старте каждого процесса он ищет профили браузеров для Claude in Chrome —
  `macos/knowledge/tcc.md`.
- Серая подпись `· summary` справа от обычного на вид текста модели — это **пересказ её рассуждения**
  (thinking-блок с подписью типа `narration`), который Claude Code рисует как реплику, а не тусклым
  курсивом; не ответ и не текст модели. Бывает у моделей, чьё рассуждение приходит пересказом
  (видели у Sonnet 5.5). Проверено по бинарнику 2.1.292: компонент `AssistantMessage` с
  `isSummary`, подпись — константа `"summary"` (07.10.2026).

## Правила

- **Баг-репорты о Claude Code** (`/feedback`) — не отправлять и не предлагать без прямой просьбы,
  не напоминать о накопленных черновиках; сохранять черновики `SendFeedback` локально можно.
  *Почему:* владелец опасается за подписку, объяснение про анонимность не убедило.
- Остальные правила — по темам: имена сессий и тики лупа — `claude-code-sessions.md`, блок
  классификатора — `claude-code-permissions.md`, фоновые вызовы модели — `claude-code-headless.md`,
  навык и команда для одного дела — `claude-code-plugins.md`, вкладки браузера —
  `claude-in-chrome.md`.
