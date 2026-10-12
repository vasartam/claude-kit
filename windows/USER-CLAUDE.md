# Слой windows

Windows 11 + WSL. Знания слоя — `knowledge/` рядом с этим файлом; читать только нужный файл. Пути проектов, ключи и сеть конкретной машины — в её правилах (личный слой или `~/.claude/CLAUDE.md`).

- Агент работает в Git Bash (инструмент Bash) и PowerShell 5.1. Git между Windows и WSL (push из WSL, worktree, `glab.exe`, rebase после Windows-git) — `git-windows.md`.
- Путь с `\` — инструментами Write/Edit, не через Bash: инструмент Bash на Windows превращает `\\` в `\`, а `\v`, `\b` в Python-строке — в управляющие символы.
- Сложная команда (вложенные кавычки, `wsl.exe` из Git Bash, PowerShell 5.1, файлы между Windows и WSL) — сначала `shell-pitfalls.md` слоя; общие грабли — `<kit>/knowledge/shell-pitfalls.md`.
