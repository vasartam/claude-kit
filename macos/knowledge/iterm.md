# iTerm2: профиль, фон, целевое приложение, вставка

iTerm2 — хост терминала на Mac владельца: в нём живут и панели мультиплексора, права macOS выданы
ему (`tcc.md`). Фон под агентами — **Background Color профиля iTerm**, а не тема мультиплексора.
Профиль один — `Default`, схема srcery (фон `#1c1b19`, с 2026-08-24; до этого `#000000`).

- **Сменить постоянно:** iTerm2 → Settings (Cmd+,) → Profiles → профиль → Colors → Background.
  Применяется сразу во всех окнах. Готовая схема `.itermcolors` — там же, Color Presets → Import.
- **Посмотреть цвет, ничего не сохраняя:** `printf '\033]1337;SetColors=bg=RRGGBB\a'` —
  собственный OSC iTerm, меняет фон текущей сессии до её закрытия.
- **Прочитать скриптом:** `plistlib` по `~/Library/Preferences/com.googlecode.iterm2.plist`,
  массив `New Bookmarks`, ключ `Background Color` (компоненты 0..1); профиль по умолчанию — по
  `Default Bookmark Guid`.
- **Писать в plist при запущенном iTerm бесполезно:** настройки он держит в памяти и перезаписывает
  файл при выходе. Скриптом — только при закрытом iTerm (Cmd+Q), затем
  `defaults read com.googlecode.iterm2 > /dev/null` (сброс кэша cfprefsd). Без остановки —
  dynamic profile: JSON в `~/Library/Application Support/iTerm2/DynamicProfiles/`, подхватывается
  на лету, но это отдельный профиль, а не правка существующего.
- **Целевое приложение** в правилах «по приложению» (автоматизации, диктовка с авто-Enter) —
  iTerm, `com.googlecode.iterm2`, а не мультиплексор: система видит только хост.
- **`Cmd+V` iTerm перехватывает сам** и вставляет только текст: картинка так до Claude Code не
  дойдёт никогда, раскладка ни при чём. Картинку вставлять `ctrl+v`
  (`<kit>/terminal/knowledge/hotkeys.md`). Если на русской раскладке не сработают ни `ctrl+v`, ни
  дубль `"ctrl+м": "chat:imagePaste"` в `~/.claude/keybindings.json` (на 2026-09-09 не проверено),
  план Б — Settings → Profiles → Keys → Key Mappings: Ctrl+М → Send Hex Code `0x16`.
