# iOS-приложение на iPhone: память, падения, профилирование

Отладка своего приложения на телефоне по кабелю, без Xcode GUI. Сборка, установка, снимки экрана и грабли —
[ios-device-dev.md](ios-device-dev.md).

- **Нехватку памяти ищи отдельно.** iOS пишет её в `JetsamEvent-<дата>.ips` без имени процесса, поэтому
  `idevicecrashreport -f <процесс>` такие отчёты пропускает. *Почему:* 2026-10-26 из-за этого пропустил, что
  приложение убили на 2 ГБ, и записал «падений нет». Список отчётов: `devicectl device info files --domain-type
  systemCrashLogs`. Копия: `devicectl device copy from --domain-type systemCrashLogs --source <имя> --destination <файл>`.
- **Как читать Jetsam.** Первая строка — заголовок, дальше JSON. Убитый процесс — `largestProcess`, у него
  `reason: per-process-limit`. Размер — `rpages` × `memoryStatus.pageSize` (16 КБ). Поле `age` — в тиках 24 МГц
  (A15), а не в наносекундах: сверяй с `age` у `kernel_task`, иначе время запуска выйдет неверным.
- **Память по секундам без Xcode.** Запись: `xcrun xctrace record --device <UDID> --template 'Activity Monitor'
  --all-processes --time-limit 120s --output a.trace`. Экспорт: `xcrun xctrace export --input a.trace --xpath
  '/trace-toc/run[@number="1"]/data/table[@schema="activity-monitor-process-live"]'`. Память — колонка
  `memory-physical-footprint`. Повторы в выгрузке заменены ссылками `ref` на элементы с `id`. Сценарий
  запускай во время записи, тогда видно, на каком шаге растёт память.
- **App Group.** `devicectl device info files --domain-type appGroupDataContainer` на iOS 17.5 у бесплатной команды
  падает с «failed to get a list of files», а `appDataContainer` работает. Запись в App Group видна в журнале:
  `cfprefsd … wrote the key(s) … group.<id>`.
- **`idevicesyslog` обрывается** («disconnected»), когда `process launch --terminate-existing` перезапускает
  приложение. Журнал включай после запуска. `-m <текст>` фильтрует по тексту сообщения.
- **Аргументы запуска — только с `--terminate-existing`.** iOS держит предзапущенный процесс приложения (prewarm,
  ~1,4 МБ), и `process launch` без флага поднимает его: `-- -Flag YES` до `UserDefaults` не доходят, хук молчит.
  2026-10-26 из-за этого «автонажатие» не сработало, а прогон без нужного состояния выглядел как «ошибка ушла».
- **Где крутится CPU.** Запись: `xctrace record … --template 'Time Profiler' --instrument 'Activity Monitor'` (CPU и
  память в одном файле). Экспорт: таблица `@schema="time-profile"` (колонки `time, thread, process, core,
  thread-state, weight, stack`; кадр — `<frame name=… addr=…><binary name=…/>`, повторы — `ref`). **Кадры своего кода
  приходят без имени бинарника** (`?`): в Debug-сборке Xcode 16+ код лежит в `<App>.app/<App>.debug.dylib`, и фильтр
  по имени бинарника их не видит — «кадров приложения нет» ещё не значит, что крутит только система. Символы:
  `xcrun atos -arch arm64 -o <App>.debug.dylib -l <база> <адреса>` (база — от адреса `_main` минус его смещение),
  системные — `-arch arm64e`, бинарник из `~/Library/Developer/Xcode/iOS DeviceSupport/<модель> <версия> (<сборка>)/Symbols/`
  + его путь на устройстве (в пути пробелы — кавычки, `~` вне их), база — «адрес − смещение» из отчёта; внутренние
  кадры SwiftUI остаются адресами. *Почему:* 2026-10-26
  записал «в цикле нет кода приложения», а после символизации там нашлись `body` своих вью — 30 % сэмплов.
- **Петля раскладки — детектор UIKit.** Аргумент запуска `-UIViewLayoutFeedbackLoopDebuggingThreshold 100`: вместо
  вечного зависания приложение падает с `NSGenericException: Degenerate layout! Layout feedback loop detected in
  subtree of <вью>`, в журнале — список вью и стеки, откуда им шлют `-setNeedsLayout`. Дешевле профиля, когда главный
  поток на 100 % и память растёт ровно.
- **Счётчик перерисовок без Instruments.** `idevicesyslog -p <процесс> -m 'key: <ключ строки>'` за 20 с и `wc -l`:
  строка экрана разрешается при каждом пересчёте `body`, в покое — единицы, в цикле — десятки тысяч. Так удобно гнать
  серию запусков и считать, в скольких случился цикл.
  Счётчик косвенный: после обновления iOS (17.5.1 → 27.0.1) в покое стало 2 вместо 9–10. «Ошибки нет» по нему —
  только вместе с независимым замером (память и CPU в Activity Monitor, детектор UIKit выше) на той же серии.
- **Форматирование Foundation проверить на Mac.** Даты и числа (`.formatted(.dateTime…)`, `.byteCount`) на macOS
  того же поколения дают то же, что на iPhone: `swift file.swift` с `Locale(identifier: "ru_RU")` отвечает за секунду
  (падеж месяца, есть ли год, «кБ»); экран на телефоне — потом, для подтверждения.
- **Prewarm видно по Jetsam.** `age` процесса (тики 24 МГц) против времени отчёта даёт момент старта: если процесс
  стартовал раньше твоего `process launch`, это был предзапуск, и аргументы до него не дошли.
- **Сначала проверь, что условие опыта сработало.** Прежде чем приписать результат флагу или состоянию, найди в
  журнале, что приложение его прочитало (`looked up value … for key <Flag>`), а для редкой ошибки гони серию с
  чередованием условий: 12 запусков без ошибки при её частоте 1/3 выпадают в 0,8 % случаев. *Почему:* 2026-10-26 по
  трём прогонам решил «цикл только при отказе VPN»; в одном из них флаг не дошёл (prewarm), а серия из 12 дала цикл
  и без отказа.
