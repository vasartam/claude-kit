# GitLab владельца: glab, CI, раннеры, SSH

Аккаунт владельца — в личном слое; проекты приватные. Ветки, ребейз, саморевью, «доехало ли», общая
рабочая копия — `git-workflow.md`. Windows-git, push из WSL, `glab.exe` — `windows/knowledge/git-windows.md`.

- Пользоваться `glab` владелец разрешил; токен (`api`, `write_repository`) — в хранилище ключей ОС.
- **Агент сам**: создаёт и настраивает проекты (`glab api -X POST projects -f name=… -f
  visibility=private`; переименование — `PUT projects/<id>` с `name` и `path`), пайплайны и
  логи, обычные переменные CI, deploy keys (публичные части), защиту веток, MR, issues.
  **Секреты в GitLab вносит только владелец.** Deploy key и
  `ci_push_repository_for_job_token_allowed` автоматический фильтр режима auto считает выдачей
  прав и блокирует — нужно явное «разрешаю» владельца в чате, после него проходит (2026-10-07).
- Ответ ревьюеру в MR — один довод и вопрос; итог правок по замечаниям — таблица «замечание → что
  изменено → коммит» и ссылка на compare: `writing-review.md`.
- Пайплайны: `glab ci list -P 20` или `glab api projects/<числовой id>/pipelines` — числовой id
  надёжнее пути с `%2F`.
- **Коммиты в личные репозитории** — от личной почты владельца (она — в правилах машины). Где
  глобальная почта git другая, задать в репозитории локально (`git config user.email …`) до первого
  коммита и проверить `git log --format=%ae`. В каждом репозитории — `.gitattributes` с
  `* text=auto eol=lf`.
- **Push висит и падает `Connection reset by 198.18.x port 22`** (VPN режет SSH на 22-м, HTTPS
  тоже рвётся, хотя `glab api` работает) — SSH через 443:
  `ssh-keyscan -p 443 altssh.gitlab.com > /tmp/altssh_kh`, сверить отпечаток ECDSA с
  `ssh-keygen -F gitlab.com -l` — должен совпасть с
  `SHA256:HbW3g8zUjNSksFbqTiUWPWg2Bq1x8xdGUrliXFzSnUw`; затем
  `GIT_SSH_COMMAND="ssh -o UserKnownHostsFile=/tmp/altssh_kh" git push ssh://git@altssh.gitlab.com:443/<аккаунт>/<проект>.git main`.
  Ключ хоста вслепую не принимать. Где 22-й порт закрыт всегда — постоянно в `~/.ssh/config`:
  `Host gitlab.com` → `HostName altssh.gitlab.com`, `Port 443`, `User git` (свой ключ —
  `IdentityFile`, `IdentitiesOnly yes`).
- Новый приватный проект создаётся и первым push по SSH (push-to-create), но через `glab api`
  надёжнее: сразу задаются описание и видимость.
- **Минуты CI ограничены**: бесплатно — 400 минут shared runners в месяц на namespace, сброс
  1-го числа; кончились — пайплайны не запускаются вовсе (ни тестов, ни автодеплоя). Пайплайн
  «тесты + деплой» на Python — около 2 мин. Поэтому пайплайн — только для пушей, которые
  трогают код (`workflow:rules`), docs-only коммиты CI не гоняют; новый каталог с кодом —
  внести в правила, иначе для него молча не будет ни тестов, ни деплоя. Плюс
  `interruptible: true` и «Auto-cancel redundant pipelines» — лишние пайплайны отменяются.
- Бесплатные раннеры gitlab.com у аккаунта работают без дополнительного подтверждения.
- **Свой раннер** (docker executor) вместо shared: все задачи — с тегом раннера; только тег
  гарантирует, что задачу не заберёт shared runner, цена — раннер лёг, пайплайн висит в
  `pending`. `docker.sock` в раннере — задача CI равна root на машине (терпимо, пока в ветку
  пушит только владелец). Конфиг с токеном — вне репозитория; `request_concurrency = 2`
  снимает жалобы на long polling. Регистрация:
  `glab api --method POST user/runners -f runner_type=project_type -f project_id=<id>` →
  `gitlab-runner register --non-interactive --url https://gitlab.com --token <токен> --executor docker …`.
- Переменная-файл с SSH-ключом, вставленная через веб-форму, может не читаться OpenSSH
  (`error in libcrypto`: CR или нет перевода строки в конце) — в job:
  `tr -d '\r' < "$VAR" > key; echo >> key; chmod 600 key`. Многострочный секрет нельзя сделать
  Masked — тип File, Protected; в job использовать только путь `$VAR`. Нужна маска — хранить
  ключ как `base64 -w0` одной строкой и декодировать в job.
- Push без запуска CI — `git push -o ci.skip` (например, перед ручным переездом сервера, чтобы
  старый путь деплоя не сработал).
- Раннеры gitlab.com — за рубежом: живые проверки российских сервисов (биржа, ЦБ, банки) в CI не
  гонять — маркер `network`, запуск локально. Корня российских сертификатов на раннерах тоже нет.
- CI для Python на uv (проверенный шаблон): образ `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`,
  `variables: {UV_CACHE_DIR: .uv-cache, UV_LINK_MODE: copy}`, `cache` с ключом по `uv.lock` на
  `.uv-cache`; шаги `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pytest -q`.
- Публичный форк на GitHub нельзя сделать приватным (отвязать — только через поддержку): перенос
  в приватный GitLab, затем форк удаляет владелец в веб-интерфейсе (удаление безвозвратное).
- Подписи соавторства Claude нет нигде — правило владельца в `USER-CLAUDE.md`; проверка перед
  push — `git-workflow.md`.
