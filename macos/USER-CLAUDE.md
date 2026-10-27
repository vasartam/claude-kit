# Слой macos

Машина на macOS. Знания слоя — `knowledge/` рядом с этим файлом; читать только нужный файл.

- Клавиши в тексте для человека — по-маковски: **Option** (⌥), **Cmd** (⌘), Ctrl, Shift; «Alt» не писать (`Ctrl+Option+T`, не `Ctrl+Alt+T`). В конфигах — синтаксис инструмента (`alt+…` в конфиге TUI и есть Option), переводить только в объяснении.
- Права macOS (микрофон, папки, экран, автоматизация) — у приложения-хоста терминала, не у `claude`; отказ в защищённой папке выглядит как «файла нет» — `tcc.md`.
- HTTPS из Python падает на сертификате — ходить системным `curl`, проверку не отключать — `python-https-curl.md`.
- Фоновый инструмент не отбирает фокус (AppleScript без `activate`) и не трогает браузер, пока человека нет за компьютером — `ui-automation.md`.
- Долгая работа без присмотра — под `caffeinate -i`, иначе машина уснёт; большие заливки — с лимитом полосы — `cloud-uploads.md`.
- Секрет из связки ключей (`security … -w`) не печатать: читать и писать одним процессом — `keychain.md`.
- Ещё в `knowledge/`: профиль и клавиши iTerm (`iterm.md`), голос Claude Code после обновления (`voice.md`), диктовка TypeWhisper (`typewhisper.md`), задания launchd (`launchd.md`), куки Chrome (`chrome-cookies.md`), группы вкладок Safari (`safari-tab-groups.md`), кэш uv (`uv-cache.md`), своё приложение на iPhone и грабли Xcode (`ios-device-dev.md`), его память, падения и профилирование (`ios-device-debug.md`), UI-тесты на телефоне (`ios-device-uitest.md`).
