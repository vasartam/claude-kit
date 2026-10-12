# Claude Code: моды

Проверено на CLI 2.1.287–2.1.288 (03–04.10.2026). Хуки-команды — `claude-code-hooks.md`.

- Мод — плагин из функций-хуков на TypeScript (навык `plugin-authoring`), грузится из каталога в
  `CLAUDE_CODE_PLUGIN_DIRS` (разделитель путей ОС, `:` / `;`; моды `<слой>/mods/*` вписывает
  `link.py`), в терминале правки подхватывает на лету. API — `types/claude-code.d.ts` навыка (grep).
- **Во вкладке Code приложения моды работают** (пробы 03.10.2026, Windows): ключ в `env`
  `settings.json` — после полного перезапуска приложения, правки мода — с новой сессией. `HOME` нет
  (`USERPROFILE`), `$.session.surface()` — `null`; `$.ui.status` и `$.ui.toast` видны, `$.prompt.submit`
  начинает ход (текст с `/` — нет), `$.session.compact` недоступен (headless), а сжимает
  `$.command.run({ command: 'compact', args })` после хода (проба 3, 04.10.2026: «Compacted session»,
  событие `session.compact` с `trigger: manual`; команда видна как набранная владельцем). `link.py`
  там — команда владельцу: настройки агенту не даёт классификатор.
- **Дописать в ответ** — `turn.step`: что выходит из хука, то показывается и записывается
  (`mods/msk-time`). Служебная строка `$.session.append` с `type: 'system'` в транскрипт ложится, но
  терминал её не рисует; тестовый набор под `$.session.append` плагина не стоит («no implementation»).
  В `claude -p` мод тоже работает — его вывод читают скрипты, проверять `CLAUDE_CODE_ENTRYPOINT` `sdk-*`.
- Тесты (`claude plugin test`, 2.1.288): заглушка движка отвечает в форме результата — `tool.call` →
  `{ result }`, вызовы → `{ value }`; хук заглушки зовёт `$` теста (свой `$` отвергнут: «module does not
  call it»); `session.compact` — `messages: [{ role, text, toolUses: [] }]`; пути на Windows — `C:\…`.
- Живая проверка мода без владельца — `claude` в псевдотерминале (Python `pty.fork`, без `HERDR_*`
  в окружении): ввод, ~30 с, `/exit`, экран — в выводе. Транскрипт такой сессии на диск не лёг.
- Валидатор: функция, которой передают `$`, — на верхнем уровне файла; хук `turn.step` —
  только `async function*`. При загрузке движок кладёт в мод
  `.claude-plugin/types/` и `tsconfig.json` — их не коммитить.
- Что знают только моды (2.1.287–2.1.288): `ui.render` для `AbovePrompt` и `Pane` —
  `props.view.agentId` (агент на экране);
  `turn.step` — `agentId`, `model`, `effort` каждого запроса; событие `session.measure` и
  `$.session.usage()` — лимиты подписки (`rateLimits` five_hour/seven_day: `percentUsed`,
  `resetsAt`). Перезапустить статусную строку мод не может.
- Цифры `session.measure` совпали с `get_usage` и статусной строкой до 1 %. Но: на `session.start`
  `rateLimits` пуст (первые — с первым ответом); окна по модели (недельный Fable) нет;
  `context.window` — окно модели, а не порог автосжатия. Порог — `usage({breakdown: 'summary'})` →
  `autoCompactThreshold`.
