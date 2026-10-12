# UI на Mac: снимки экрана, System Events, браузер из фоновых инструментов

## Снимки экрана и System Events

Нужны права хоста терминала (`tcc.md`): Запись экрана и Универсальный доступ. Выдаёт владелец сам;
на его Mac выданы iTerm2 (2026-09-25).

- `screencapture -x -t jpg <файл>` — снимок без звука, смотреть через Read. Одно окно —
  `screencapture -R<x>,<y>,<w>,<h>` по геометрии из
  `osascript -e 'tell application "System Events" to get {position, size} of window 1 of process "<имя>"'`.
  Без права — «could not create image from display».
- System Events видит процессы и окна. `click at {x,y}` по окну **Electron**-приложения один раз
  проходит, дальше висит с «Время AppleEvent истекло (-1712)» — на кликах по Electron не строить;
  такие настройки владелец меняет сам по шагам от агента.
- Синтетические клики (свой кликер на Swift/CGEvent) блокирует классификатор auto mode — не
  обходить. `cliclick` и pyobjc Quartz не установлены.
- Снимок показывает всё, включая чужие окна и другие сессии владельца: снимать только по делу.

## Фоновый инструмент и браузер

Петля повтора, которая дёргает браузер или другой UI, без человека копит мусор: *случай* — сторож
сессии дев-сервера звал вход в Chrome раз в две минуты безусловным `open`; вход без человека не
случался, за ночь на неспящей машине набежало два десятка вкладок. Вопрос при написании и ревью
такой петли: что она сделает, повторившись сто раз без человека? Ответ «сто вкладок» — нужны две
вещи.

1. **Переиспользовать вкладку и не отбирать фокус.** Искать открытую вкладку AppleScript-ом, новую —
   только если нет; `activate` не звать. Нет доступа к Chrome (TCC) или он закрыт — фолбэк `open`.
   Automation-доступ к Chrome из терминала работает (2026-09-04). Функция — ниже.
2. **Не трогать браузер, пока человека нет.** Секунды простоя клавиатуры и мыши:
   `ioreg -c IOHIDSystem | awk '/HIDIdleTime/ { gsub(/[^0-9]/,"",$NF); print int($NF/1e9); exit }'`
   (пусто — ioreg не ответил). Простой больше ~3 мин — ждать возвращения.

Дев-сервер, который сам открывает вкладку при старте, — гасить это (`CI=1` у rsbuild) и показывать
адрес своей переиспользующей функцией.

```bash
show_in_chrome() {   # <подстрока своей вкладки> <адрес для новой>
  osascript - "$1" "$2" >/dev/null 2>&1 <<'APPLESCRIPT' && return 0
on run argv
  set theMatch to item 1 of argv
  set theURL to item 2 of argv
  if not (application "Google Chrome" is running) then error "chrome not running"
  tell application "Google Chrome"
    repeat with w in windows
      set i to 0
      repeat with t in tabs of w
        set i to i + 1
        if URL of t contains theMatch then
          set active tab index of w to i
          set index of w to 1
          return "reused"
        end if
      end repeat
    end repeat
    if (count of windows) is 0 then
      make new window
      set URL of active tab of front window to theURL
    else
      make new tab at end of tabs of front window with properties {URL:theURL}
    end if
    return "opened"
  end tell
end run
APPLESCRIPT
  open -a "Google Chrome" "$2" 2>/dev/null || open "$2"
}
```
