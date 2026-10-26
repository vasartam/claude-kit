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
  thread-state, weight, stack`; кадр — `<frame name=… addr=…><binary name=…/>`, повторы — `ref`). Системные
  фреймворки на устройстве без символов (адреса), но имя бинарника есть: если в горячих стеках нет ни одного кадра
  из бинарника приложения, крутит сам SwiftUI/UIKit (цикл раскладки), а не код приложения.
