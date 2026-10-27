# knowledge — общие знания для любого проекта

Знания вне одного проекта. Читать **только нужный файл**. Метод разработки — навык
`skills/dev-method`; факты проекта — его `docs/`; знания слоёв — индексы внизу.

## Владелец и работа с ним

| Файл | Что внутри | Когда читать |
|---|---|---|
| [owner.md](owner.md) | язык, голосовой ввод, что владелец ценит и чего не делать | в начале работы, перед отчётом |
| [answer-style.md](answer-style.md) | вывод первой строкой, ссылки вместо id, отчёт-Artifact, метка и слово прощания | перед итоговым ответом |
| [autonomy.md](autonomy.md) | что сам, что после «да», что только владелец; «владелец ушёл» | перед тратой, секретом, рискованной правкой |
| [writing.md](writing.md) | письмо людям от имени владельца, сводки для пересылки | перед текстом людям (черновик — `message-draft`) |
| [writing-review.md](writing-review.md) | ответ ревьюеру, итог правок по замечаниям | перед комментарием в MR |
| [writing-docs.md](writing-docs.md) | факты по первоисточнику, «что проверено», без локальных путей | перед README, описанием MR, документом |
| [subagents.md](subagents.md) | когда окупается, модель, цена, задание и проверка | перед делением работы |
| [session-hygiene.md](session-hygiene.md) | когда и как выносить знания, конец сессии | по ходу сессии, на пороге контекста |
| [tooling-repos.md](tooling-repos.md) | свой тулинг и знания — в какой репозиторий; поручение сессии на другой машине | перед новым навыком, хуком, правилом; передача другой машине |
| [project-setup.md](project-setup.md) | `CLAUDE.md` → `@AGENTS.md`, указатель на набор, `docs/`, CI | при создании проекта |

## Claude Code

| Файл | Что внутри | Когда читать |
|---|---|---|
| [claude-code.md](claude-code.md) | указатель тем и файлов слоёв; MCP, настройки, Bash без TTY | при настройке — начать отсюда |
| [claude-code-instructions.md](claude-code-instructions.md) | личные навыки, бюджет списка навыков, `CLAUDE.md` и `AGENTS.md`, импорты `@`, память | при правке навыков, инструкций, памяти |
| [claude-code-sessions.md](claude-code-sessions.md) | effort, workflow и ultracode, облачные сессии, имена | при заведении сессии, в лупе |
| [claude-code-plugins.md](claude-code-plugins.md) | namespace, кэш, `/reload-plugins`, суффикс `-cmd` | при работе с плагином |
| [claude-code-hooks.md](claude-code-hooks.md) | события хуков, `PreCompact`, `asyncRewake`, exec-форма | при написании хука |
| [claude-code-mods.md](claude-code-mods.md) | моды: `CLAUDE_CODE_PLUGIN_DIRS`, приложение, что знают только моды, сжатие из мода | при написании мода |
| [claude-code-context.md](claude-code-context.md) | заполнение контекста, автосжатие, транскрипт, цена старта | при подсчёте контекста |
| [claude-code-permissions.md](claude-code-permissions.md) | `permissions`, классификатор auto mode, `autoMode.allow` | отказ классификатора, нужны права |
| [claude-code-headless.md](claude-code-headless.md) | `claude -p`: вложенный запуск, детерминированный прогон | инструмент зовёт модель |
| [claude-in-chrome.md](claude-in-chrome.md) | «not connected», группа вкладок, снимок владельцу, прямые ссылки | перед работой в браузере |
| [claude-in-chrome-input.md](claude-in-chrome-input.md) | клики после ресайза, фокус, paste в мессенджер, select | кликаешь и вводишь текст |
| [claude-in-chrome-data.md](claude-in-chrome-data.md) | `[BLOCKED]`, localStorage и токены, fetch на localhost, таймеры вкладки | забираешь данные со страницы |
| [claude-models.md](claude-models.md) | лимиты тарифа, модель и effort под задачу, кэш и цены | при выборе модели, на исходе лимита |
| [model-advice.md](model-advice.md) | совет по модели и effort: строка хука `model-advisor`, когда молчать, лимиты и норма недели | строка `model-advisor` к промпту, задача сменилась |

## Разработка, git, оболочка

| Файл | Что внутри | Когда читать |
|---|---|---|
| [git-gitlab.md](git-gitlab.md) | `glab`, identity коммитов, SSH через altssh:443, CI, раннер | при создании проекта, CI, сбое SSH |
| [git-history-rewrite.md](git-history-rewrite.md) | filter-branch без filter-repo, проверка, force push в защищённую ветку, housekeeping | вычистить файлы или подписи из истории |
| [git-workflow.md](git-workflow.md) | rebase, force-with-lease, конфликты, саморевью, «доехало ли» | перед ребейзом, push, MR |
| [shell-pitfalls.md](shell-pitfalls.md) | кавычки через stdin, `docker run --rm`, секреты в выводе, `awk` при русской локали | перед сложной командой, ssh, docker |
| [web-ui.md](web-ui.md) | скриншоты безголовым Chromium, CSP, конкурс дизайнов | при вёрстке и её проверке |
| [analysis.md](analysis.md) | целостность данных, протокол поиска стратегии, знаменатель | перед анализом данных |
| [scraping.md](scraping.md) | квоты, эпохи полей, SQLite WAL, HAR и токены | при написании сборщика |

Знания под личные проекты (серверы и сайты, Telegram, видео, брокеры, картинки) — в личном слое
машины, если он подключён (`tooling-repos.md`).

## Знания слоёв

Читать, когда слой подключён (его `USER-CLAUDE.md` в правилах сессии) или вопрос о такой машине.

| Индекс | Что внутри |
|---|---|
| [terminal](../terminal/knowledge/README.md) | терминал и herdr: сжатие и закрытие, перезапуск, статусная строка, хоткеи, утилиты |
| [desktop](../desktop/knowledge/README.md) | приложение Claude: перезапуск, сон и будильники, окружение вкладки Code |
| [macos](../macos/knowledge/README.md) | TCC, iTerm, голос, UI-автоматизация, Keychain, куки Chrome, launchd |
| [windows](../windows/knowledge/README.md) | Windows 11 + WSL, Git Bash, PowerShell, push из WSL |

Новый файл — короткой строкой в индекс своего слоя; индекс, как и файл, ≤ 8 КБ.
