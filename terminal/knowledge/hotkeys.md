# Хоткеи: herdr, Claude Code, русская раскладка

## herdr

- Хоткеи — секция `[keys]` в `~/.config/herdr/config.toml`; полный список действий с дефолтами —
  `herdr --default-config`. Префикс — `ctrl+b`, нажимается отдельно до второй клавиши. Дефолты
  закрытия: `close_tab = "prefix+shift+x"`, `close_pane = "prefix+x"`,
  `close_workspace = "prefix+shift+d"`.
- Действие принимает **массив**: `close_tab = ["prefix+shift+x", "ctrl+shift+x"]` вешает оба, дефолт
  не теряется. Одиночная строка **заменяет** дефолт. В `--default-config` форма массива не описана,
  проверена: `herdr config check` разбирает массив поэлементно и мусорный элемент называет поимённо
  (`invalid keybinding: keys.close_tab = "..."; disabling binding`).
- Надёжно ловятся `ctrl+<буква>` и функциональные клавиши; `alt+…`, `cmd/super` и пунктуация с
  модификаторами зависят от терминала. На macOS `alt` в конфиге — клавиша Option, а Cmd-сочетания,
  занятые самим терминалом (iTerm: `Cmd+W` и др.), до herdr не доходят; свободные (`cmd+alt+left/right`)
  — доходят.
- `move_tab_previous` / `move_tab_next` есть, но по умолчанию не забинжены (`""`) — кажется, что
  функции нет. Порядок агентов в сайдбаре — `[ui] agent_panel_sort` (`"spaces"` / `"priority"`).
  Сбросить все свои биндинги — `herdr config reset-keys`.
- Свои команды — `[[keys.command]]` с `type = "shell"`. Пути в них полные (`$HOME/...`): PATH
  сервера herdr не гарантирован. Ошибку смотреть ручным повтором команды: в
  `herdr-server.log` у `agent.start` только `outcome="error"`.

### Ctrl+Shift+T — новая вкладка сразу с Claude

Обычная вкладка (`prefix+c`, плюсик в UI) остаётся чистым шеллом. Имя агента обязано быть
уникальным (иначе `agent_name_taken`, вкладка пустая) и подходить под `^[a-z][a-z0-9_-]{0,31}$` —
берём id панели (`w3:p2C` → `claude-p2c`). Сразу после `tab create` шелл ещё стартует, и
`agent start` отвечает `agent_pane_busy`; herdr эту ошибку сам не повторяет (сбой ~1 из 14 нажатий),
поэтому команда повторяет до 10 с. Альтернатива через `default_shell` отвергнута: Claude полез бы во
все сплиты и задвоился бы с `resume_agents_on_restore`.

```toml
[[keys.command]]
key = "ctrl+shift+t"
type = "shell"
command = 'H=$HOME/.local/bin/herdr; p=$($H tab create --focus | jq -r .result.root_pane.pane_id) || exit 1; n="claude-$(echo "${p##*:}" | tr A-Z a-z)"; i=0; while r=$($H agent start "$n" --kind claude --pane "$p" 2>&1); case $r in *agent_pane_busy*) [ $((i+=1)) -lt 40 ];; *) false;; esac; do sleep 0.25; done; case $r in *\"error\"*) echo "$(date "+%F %T") $p $r" >> $HOME/.config/herdr/claude-tab.log;; esac'

# Ctrl+Shift+R — перезапуск Claude в панели в фокусе с тем же разговором (restart-in-place.md)
[[keys.command]]
key = "ctrl+shift+r"
type = "shell"
command = "$HOME/.claude/bin/claude-restart --focused"
```

`jq` не в PATH сервера — вписать полный путь. Проверить ветку повтора — занять панель
`herdr pane run <id> "sleep 4"`.

## Русская раскладка

В TUI (herdr, Claude Code) при русской раскладке ломается не всё, а по границе:
- `ctrl+<буква>` — управляющий код (`ctrl+b` → 0x02), раскладка не влияет. Под сомнением: владелец
  видел, что `ctrl+v` (вставка картинки) сработал только на английской; `keyprobe.py` не запускали.
  На проверку в `~/.claude/keybindings.json` добавлен блок `Chat` с `"ctrl+м": "chat:imagePaste"` —
  вживую не проверен (09.09.2026); пока не проверено, обе формулировки держать рядом. Не сработает —
  план Б в настройках терминала (iTerm — `<kit>/macos/knowledge/iterm.md`);
- `ctrl+shift+<буква>` и **одиночные буквы** (вторая клавиша после префикса, `y`/`n` в диалогах,
  `j`/`k` в списках) ломаются: терминал в kitty keyboard protocol шлёт кодпоинт текущей раскладки
  (`ч` вместо `x`).

Лечится дублями на кириллицу той же физической клавиши (ЙЦУКЕН): `x→ч`, `b→и`, `c→с`, `j→о`, `k→л`,
`y→н`, `n→т`, `q→й`, `g→п`, `s→ы`, `t→е`, `r→к`.
- **herdr** — массив на действие: `close_tab = ["prefix+shift+x", "prefix+shift+ч", "ctrl+shift+x",
  "ctrl+shift+ч"]`. Одиночная строка заменяет дефолт, поэтому продублированные действия выписывать
  целиком (секцию `[keys]` проще выписать всю). Кириллицу `config check` принимает. Для своих
  команд — второй `[[keys.command]]` с `ctrl+shift+е`, `ctrl+shift+к`.
- **Claude Code** — `~/.claude/keybindings.json`: `{"bindings": [{"context": "Chat", "bindings":
  {"<клавиша>": "<действие>"}}]}`; кириллический символ работает как обычная клавиша. Свои биндинги
  **добавляются** к дефолтам (снять дефолт — `null`). Контексты и действия — из навыка
  `keybindings-help`. Вставка картинки — `chat:imagePaste`, дефолт `ctrl+v` (Windows/WSL — `alt+v`);
  на macOS `Cmd+V` перехватывает терминал и вставляет только текст — `<kit>/macos/knowledge/iterm.md`.
- Централизованно в настройках терминала не решить: одиночные буквы не перехватить, не сломав ввод.

## Что реально шлёт терминал

`~/.claude/bin/keyprobe.py` — raw-режим с kitty protocol, печатает байты каждого нажатия; выход —
`Ctrl+C`. Запускать **вне herdr**, иначе herdr перехватит сочетание раньше.
