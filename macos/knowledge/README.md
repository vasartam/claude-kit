# knowledge слоя macos

Читать только нужный файл. Общие знания — `<kit>/knowledge/README.md`.

| Файл | Что внутри | Когда читать |
|---|---|---|
| [tcc.md](tcc.md) | права macOS — у приложения-хоста терминала, кэш TCC до Cmd+Q, защищённые папки (отказ выглядит как «нет файла»), три службы TCC, диалог «доступ к данным других приложений» от claude, журнал tccd и TCC.db | при отказе в доступе, диалоге прав, «не работает в одной панели» на Mac |
| [voice.md](voice.md) | голосовой ввод Claude Code умирает после обновления: механизм (удалённый инод, tccd, coreaudiod), проверка процесса, что не помогает (`killall coreaudiod` — хуже) | «голос не работает» на Mac (лечение — `terminal/knowledge/updates.md`) |
| [typewhisper.md](typewhisper.md) | локальная диктовка TypeWhisper: движок и модель, грабли (нет ru у SpeechAnalyzer, Terms вредят Whisper, живого превью нет, авто-Enter через workflow), локальный API, расшифровка записи кусками по 45 с | при настройке голосового ввода на Mac, расшифровке аудио |
| [iterm.md](iterm.md) | фон под агентами — профиль iTerm2, проба цвета OSC 1337, чтение plist, почему не писать в plist при живом iTerm, dynamic profile, bundle id для правил по приложению | при настройке iTerm |
| [ui-automation.md](ui-automation.md) | screencapture и System Events, клики по Electron виснут, синтетические клики режет классификатор; фоновый инструмент и браузер — вкладка через AppleScript без activate, простой ввода через ioreg | при снимках экрана, UI-скриптинге, фоновой петле, которая открывает браузер |
| [launchd.md](launchd.md) | задания launchd: запуск без ожидания расписания (`kickstart`), ИИ-шаги с `claude -p` проверять только так, пустое окружение, выгрузка ненужного (`bootout`) | при настройке и проверке фонового задания на Mac |
| [python-https-curl.md](python-https-curl.md) | Python с python.org не видит keychain → CERTIFICATE_VERIFY_FAILED; HTTPS через системный curl, пакетный режим, скачивание во временное имя | перед питоновским кодом, который ходит по HTTPS на Mac |
| [keychain.md](keychain.md) | security find/add-generic-password без печати секрета; OAuth MCP в записи «Claude Code-credentials» и лечение «invalid_client: unknown client_id» | при работе со связкой ключей, сбое входа в MCP с OAuth |
| [chrome-cookies.md](chrome-cookies.md) | расшифровка кук Chrome на macOS (Keychain → PBKDF2 → AES-CBC, 32 байта хеша домена), копия базы, суффиксы домена, ненайденное в .env затирать | когда нужна HttpOnly-сессия из браузера владельца |
| [cloud-uploads.md](cloud-uploads.md) | большие заливки: лимит полосы по расписанию, один поток, caffeinate, части по 2 ГБ с SHA256SUMS, rclone check до удаления; rclone и облако Mail.ru — три грабли и WebDAV | перед выгрузкой больших объёмов в облако |
| [uv-cache.md](uv-cache.md) | ~/.cache/uv пухнет от uvx-MCP: uv cache prune --force, лок живых процессов, хардлинков нет | при нехватке места на диске, чистке кэшей |
