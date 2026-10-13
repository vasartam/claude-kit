# Git и GitLab на Windows: Windows-git, push из WSL, glab.exe

Общее про GitLab владельца — `knowledge/git-gitlab.md`, ветки и рабочая копия —
`knowledge/git-workflow.md`. Здесь — то, что верно только на домашней Windows-машине.

- **`glab`** — `%LOCALAPPDATA%\Programs\glab\glab.exe`: если `command -v glab` пусто (в PATH не
  всегда), вызывать по полному пути; токен — в keyring Windows. Путь проекта с `%2F` из
  PowerShell молча возвращает пустой список — брать числовой id.
- **Коммит** — Windows-git (identity владельца уже в глобальном конфиге). `core.autocrlf=true`,
  поэтому в каждом репозитории `.gitattributes` с `* text=auto eol=lf`. В WSL `autocrlf=true`
  тоже: без `.gitattributes` `pull` из WSL переписал `.sh` на CRLF, и bash упал на
  `set: pipefail` с `\r` на конце (13.10.2026). WSL-git на `/mnt/d` к тому же видит слетевший
  бит исполнения как правку и не даёт `pull` — в такой копии `git config core.fileMode false`.
- **`error: <файл>: does not match index`** у Windows-git после работы WSL-git с той же копией —
  устаревший кэш stat: `git update-index -q --refresh`, затем повторить.
- **Push — только из WSL** (ключ GitLab только там):
  `MSYS_NO_PATHCONV=1 wsl.exe -d Debian -e sh -c 'cd /mnt/d/dev/<проект> && git -c safe.directory=/mnt/d/dev/<проект> push origin main'`.
  Другой путь — Windows-git с транспортом из WSL: `GIT_SSH_COMMAND="wsl -d Debian -- ssh" git push`.
  Он нужен, когда хуки есть только в Windows-git (например, `git-lfs`), и для push из worktree:
  git в WSL не понимает `.git`-файл worktree с Windows-путём — пушить Windows-git'ом из самого
  worktree.
- **`pull --rebase` из WSL после коммита Windows-git** может встать посреди rebase с «Your
  local changes … would be overwritten» (два git по-разному видят файлы — кэш stat, концы
  строк), хотя правок нет. Не `reset`: `git status` в Windows-git, затем там же
  `GIT_EDITOR=true git rebase --continue`, конфликты — руками, тесты — заново.
- Push висит и падает `Connection reset by 198.18.x port 22` — SSH через 443
  (`knowledge/git-gitlab.md`); при странных отказах сначала проверить сеть машины (её правила).
