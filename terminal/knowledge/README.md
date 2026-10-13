# knowledge слоя terminal — Claude Code в терминале и herdr

Читать только нужный файл. Общие знания — `<kit>/knowledge/README.md`; хуки контекста, модели и лимита — `<kit>/hooks/README.md`, хук слоя — `../hooks/README.md`.

| Файл | Что внутри | Когда читать |
|---|---|---|
| [session-compact.md](session-compact.md) | сжатие `claude-compact` (инструкция ≤ ~250 символов, файл `after-compact.md`, продолжение после сжатия, флаги), кто зовёт `/session-extract-knowledge` и какой концовки ждёт, закрытие `claude-close`, вне herdr | перед сжатием и закрытием сессии в терминале |
| [owner-commands.md](owner-commands.md) | команды владельца через `!`: классификатор их не проверяет, одна строка, длинное — заранее в файл, без TTY, интерактивное — в отдельной вкладке | когда команду должен запустить владелец |
| [herdr.md](herdr.md) | herdr: config.toml и session.json, config check и reload-config без перезапуска, сайдбар, тема (красит только UI herdr), плагины (окружение сервера, обновление на лету, install и link), перезапуск сервера, доступ с телефона (/rc, SSH) | при настройке herdr, когда «не применилось» |
| [hotkeys.md](hotkeys.md) | хоткеи herdr (массивы, префикс, [[keys.command]]), Ctrl+Shift+T — вкладка с Claude, Ctrl+Shift+R — перезапуск, русская раскладка и кириллические дубли, keybindings.json Claude Code, keyprobe | при настройке хоткеев, «на русской раскладке не работает» |
| [herdr-naming.md](herdr-naming.md) | метка вкладки (когда и как переименовывать), откуда строки сайдбара, подмена заголовка именем сессии, плагины автоименования и их патчи | при переименовании вкладки и правке автоименования |
| [restart-in-place.md](restart-in-place.md) | панель — шелл, перезапуск агента на месте, что переживает --resume, однострочные промпты, окно при запуске, claude-restart и local-файл машины, фоновая сессия (claude attach) | перед перезапуском сессии, при потере фоновых задач |
| [updates.md](updates.md) | живые панели на старой версии, нативный установщик и installMethod, голос умер после обновления, autoUpdates | «работает только в новой вкладке», «голос не работает» |
| [statusline.md](statusline.md) | claude-statusline: подключение, как выглядит и почему, откуда данные (session-status, лимиты, дневная норма, ultracode, оценка после сжатия), что она пишет хукам, мод agent-view-status | при правке статусной строки и хуков, которые читают её файлы |
| [tools.md](tools.md) | утилиты terminal/bin (claude-compact, claude-close, claude-restart, claude-autocontinue, claude-prompt, claude-usage-report, claude-live-tasks, keyprobe), зависимости, шим herdr-namer-claude, что link.py не делает | при вызове утилит и установке слоя terminal |
