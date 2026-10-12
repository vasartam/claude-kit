# launchd: фоновые задания по расписанию

Свои фоновые инструменты на Mac живут заданиями launchd (`~/Library/LaunchAgents/<label>.plist`).
Метка задания — `<label>`, домен пользователя — `gui/$(id -u)`.

- **Запустить сейчас, не дожидаясь расписания:** `launchctl kickstart -k gui/$(id -u)/<label>`
  (`-k` — сначала убить идущий экземпляр). Состояние, код последнего выхода, окружение —
  `launchctl print gui/$(id -u)/<label>`.
- **ИИ-шаги инструментов, которые зовут `claude -p`, проверять только так.** Из сессии Claude Code
  вложенный `claude -p` получает пустой промпт и выходит с кодом 0 —
  `<kit>/knowledge/claude-code-headless.md`.
- **Под launchd окружение пустое:** переменных сессии нет (`CLAUDECODE` и прочие — читать с
  умолчанием, `claude-code-headless.md`), `PATH` — системный минимум (`/usr/bin:/bin:/usr/sbin:/sbin`):
  утилиты Homebrew звать полным путём или задать `PATH` в `EnvironmentVariables` plist. Из сессии
  этого не видно — там всё задано.
- **Инструмент больше не нужен — выгрузить и задание:** `launchctl bootout gui/$(id -u)/<label>` и
  удалить plist. Задание с `KeepAlive` иначе крутится и пишет лог дальше, хотя про инструмент уже
  забыли.
