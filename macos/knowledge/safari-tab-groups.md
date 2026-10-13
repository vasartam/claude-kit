# Группы вкладок Safari

В словаре AppleScript Safari групп вкладок нет: ни списка, ни создания. Работает так (проверено 13.10.2026, Safari на macOS 26).

## Прочитать

Группы лежат в `~/Library/Containers/com.apple.Safari/Data/Library/Safari/SafariTabs.db` (SQLite, читать с `?mode=ro`):
группа — строка `bookmarks` с `type=1` и `parent=0`, её вкладки — строки с `parent=<id группы>` (`title`, `url`).
Писать в базу нельзя: её синхронизирует iCloud.

## Создать

1. `tell application "Safari" to activate` и `make new document` — новое окно, чтобы не переключать группу
   в окне, где человек работает.
2. Через System Events нажать пункт меню «Файл» → «Новая группа пустых вкладок»
   (`menu item … of menu 1 of menu bar item 3`). После этого фокус стоит в поле имени группы в боковой панели.
3. Ввести имя через буфер: `keystroke` с кириллицей и длинным тире зависит от раскладки. Сохранить буфер,
   Cmd+A, Cmd+V, Return (`key code 36`), вернуть буфер.
4. Вкладки открывать обычным AppleScript в `front window`: `set URL of current tab`, `make new tab with properties {URL:…}`.
   Они попадают в активную группу окна.
5. Проверить по базе: группа с нужным `title` и её вкладки.

Нужны права хоста терминала на Универсальный доступ и на управление Safari (`tcc.md`).
