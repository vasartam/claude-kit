# herdr: настройка, тема, плагины, доступ с телефона

herdr — терминальный менеджер воркспейсов для ИИ-агентов: спейсы, вкладки и панели, агенты в
левом сайдбаре. Бинарь обычно `~/.local/bin/herdr`. Хоткеи — [hotkeys.md](hotkeys.md),
автоименование вкладок — [herdr-naming.md](herdr-naming.md), перезапуск агента в панели —
[restart-in-place.md](restart-in-place.md).

## Файлы и как применять правки

- `~/.config/herdr/config.toml` — единственный пользовательский конфиг (тема, `[keys]`, `[ui]`);
  в нём только переопределения. Полный список опций с комментариями — `herdr --default-config`.
- `~/.config/herdr/session.json` — состояние сессии; **перекрывает** конфиг для того, что менялось
  мышью (`sidebar_width`, `sidebar_section_split`). Переживает перезапуск, сбрасывается только при
  пересоздании сессии.
- Логи — `~/.config/herdr/herdr-server.log`, `herdr-client.log`; сокеты API — `herdr.sock`,
  `herdr-client.sock`.
- После правки: `herdr config check` (ждём `config: ok`), затем `herdr server reload-config`
  (`status: applied`) — без перезапуска.
- herdr сам переписывает `config.toml`, когда настройки меняют из UI: сторонние комментарии и
  форматирование могут пропасть, значения — измениться между двумя чтениями.
- Живое состояние — `herdr api snapshot` (ширины сайдбара там нет — она в `session.json`).
- Для ИИ-агентов: `herdr --skill` печатает скилл-файл управления панелями и агентами.
- Интеграция с Claude Code — `herdr integration install claude`: свой хук `SessionStart` в
  `~/.claude/settings.json` (отчёт о сессии — по нему herdr следит за агентом, восстанавливает его и
  именует вкладку), проверка — `herdr integration status`. Ведёт её herdr, набор не дублирует
  (`<kit>/terminal/hooks/README.md`).
- Ширина сайдбара: `sidebar_width` (стартовая) и коридор `sidebar_min_width`/`sidebar_max_width` —
  в нём работают и автоподгонка под имена спейсов, и растягивание мышью. min = max = width
  фиксирует ширину и выключает автоподгонку.

## Тема

Тема красит **только интерфейс herdr** (сайдбар, рамки, панели). Всё внутри пейна (шелл, claude,
любой TUI) рисует хост-терминал фоном своего профиля — у herdr настройки для этого нет («keep the
host terminal background»). Фон вокруг панелей — `[theme.custom]` в herdr, фон внутри пейна —
профиль терминала.

- Встроенные темы (`theme.name`): `catppuccin`, `catppuccin-latte`, `terminal`, `tokyo-night`,
  `tokyo-night-day`, `dracula`, `nord`, `gruvbox`, `gruvbox-light`, `one-dark`, `one-light`,
  `solarized`, `solarized-light`, `kanagawa`, `kanagawa-lotus`, `rose-pine`, `rose-pine-dawn`,
  `vesper`. Своих тем из файлов нет: неизвестное имя → `unknown theme, falling back`.
- Своя тема — только `[theme.custom]` поверх базовой. Токены: фоны `panel_bg`, `sidebar_bg`,
  `active_row_bg`, `selection_bg`; поверхности `surface0`, `surface1`, `surface_dim`, `overlay0`,
  `overlay1`; текст `text`, `subtext0`; `accent`; палитра `mauve`, `green`, `yellow`, `red`, `blue`,
  `teal`, `peach`. Значения — `#rrggbb`, имена цветов, `rgb(r,g,b)`, сброс `reset`/`default`/`none`/
  `transparent` (= фон хост-терминала).
- Достоверный список ключей — `config-reference.json` в репозитории herdr под тегом своей версии
  (`herdr --version`), не догадки.

## Плагины: окружение и обновление на лету

- Команды плагинов запускает **сервер**, и они наследуют **его** окружение. `export` в `~/.zshrc`
  попадает только в шелл панели: до плагина переменная дойдёт после перезапуска сервера. Секции env
  у плагинов нет (ни в `config.toml`, ни в манифесте `herdr-plugin.toml`) — настройку из env стоит
  продублировать дефолтом в самом плагине.
- Сервер спавнит команду плагина **заново на каждое событие** (`herdr plugin log list`): правка
  скрипта или пересборка бинаря подхватывается со следующего события, без перезапуска.
  `herdr server reload-config` перечитывает конфиг, но не окружение.
- `herdr plugin install <owner>/<repo>` каждый раз качает апстрим в новый каталог
  `~/.config/herdr/plugins/github/<repo>-<хеш>/` (правки в старом перестают действовать);
  `herdr plugin link <path>` регистрирует плагин по пути, не качает и не выполняет build-шаги —
  собирать самому. Настройки плагина — `~/.config/herdr/plugins/config/<id>/`, переживают
  переустановку.

## Перезапуск сервера

`herdr server stop` — перерыв в работе агентов: процессы в панелях гибнут. Восстанавливаются
воркспейсы, вкладки, панели, cwd, раскладка, фокус и диалоги агентов через `claude --resume` /
`codex resume` (`resume_agents_on_restore` в `[session]`, по умолчанию включён, нужна интеграция
с Claude Code — выше). Экран панели не возвращается без `pane_history = true` в
`[experimental]`. `herdr update --handoff` (экспериментально) передаёт живые PTY новому серверу —
только для установок под собственным апдейтером herdr.

## С телефона

1. **Remote Control** — встроен в CLI, Desktop не нужен: `/remote-control` (`/rc`) в идущей сессии
   или `claude --remote-control [имя]` для новой. Сессия появляется в мобильном приложении Claude
   (раздел Code) и на claude.ai/code. Нужен вход через аккаунт claude.ai (не API-ключ), включается на
   каждую сессию; админ организации или сеть могут его закрыть — тогда второй путь.
2. **herdr по SSH**: мобильного клиента нет, но сервер персистентный — `herdr --remote <ssh-target>`,
   `herdr session attach <name>`. Удалённый вход на машину + Tailscale + Termius/Blink → `herdr`
   открывает все вкладки.
