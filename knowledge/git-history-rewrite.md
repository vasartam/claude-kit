# Git: переписать историю — вычистить файлы и подписи

Проверено 03.10.2026 на наборе (84 → 74 коммита). `git filter-repo` может не стоять — хватает
`git filter-branch` в **отдельном клоне** (рабочую копию, на которую смотрят ссылки, не трогать):
- `FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f --prune-empty --index-filter "git rm -rq
  --cached --ignore-unmatch <пути>" --msg-filter "sed -E '/^Co-Authored-By: Claude/Id' | git
  stripspace" -- main`; пути — все, под которыми файл жил (`git log --format= --name-only | sort -u`).
- Проверить: `git diff <старый> <новый>` пуст (итоговые файлы те же), в `git log --name-only` путей нет.
- Push — `git push --force-with-lease=main:<старый sha> origin main`. `pre-receive hook declined` —
  ветка защищена: владелец включает Settings → Repository → Protected branches → Allowed to force
  push, после push — выключает. Другие клоны: нет своих незапушенных — `git fetch && git reset --hard
  origin/main`.
- Старые коммиты GitLab отдаёт по хешу до чистки: Settings → General → Advanced → Housekeeping →
  Prune unreachable objects.
- Текст внутри старых версий оставшихся файлов так не убрать; к публикации надёжнее одна свежая история.
