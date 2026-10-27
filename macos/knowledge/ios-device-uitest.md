# UI-тесты XCUITest на своём iPhone

Когда проверку на телефоне нужно провести без человека: нажатия внутри экранов, свайпы, системные запросы, Safari.
Сборка и установка — [ios-device-dev.md](ios-device-dev.md), падения и журнал — [ios-device-debug.md](ios-device-debug.md).

## Цель и запуск

- Цель в XcodeGen — `type: bundle.ui-testing`, `TEST_TARGET_NAME: <приложение>`, отдельная схема только с этой целью
  (иначе `xcodebuild test` тащит юнит-тесты и их пакеты). Исходники можно держать вне git (в игнорируемом `build/`).
- **Бесплатный Apple ID подходит:** раннер `<bundle>.uitests.xctrunner` подписывается той же командой
  (`-allowProvisioningUpdates`), отдельного «Доверять» не нужно. Приложение ставится поверх, данные остаются.
- Swift 6 со строгой конкурентностью не собирает обычный код теста (`cannot form key path to main actor-isolated
  property 'label'`): для цели тестов — `SWIFT_VERSION: 5.0`, `SWIFT_STRICT_CONCURRENCY: minimal`.
- Запуск: `xcodebuild test -scheme <UITests> -destination 'platform=iOS,id=<UDID>' -resultBundlePath r.xcresult
  -only-testing:<Цель>/<Класс>/<тест>`. Снимки (`XCTAttachment(screenshot:)`, `lifetime = .keepAlways`) достать:
  `xcrun xcresulttool export attachments --path r.xcresult --output-path dir`; имена — в `dir/manifest.json`
  (`suggestedHumanReadableName`). Точку в имени вложения не ставить: всё после неё уходит в расширение.
- Итоги удобнее печатать строками с меткой (`print("NOTE …")`) и грепать из журнала `xcodebuild`; дерево
  доступности — `app.debugDescription`. Сначала один пробный тест с дампом дерева, потом проверки.
- Аргументы запуска приложения — `app.launchArguments = ["-Ключ", "значение"]` (читаются из `UserDefaults`);
  крупный шрифт только для приложения — `-UIPreferredContentSizeCategoryName UICTContentSizeCategoryXXL`.

## Что работает не так, как ждёшь (iOS 27, 2026-10-27)

- **Раннер не может писать в общий буфер:** `UIPasteboard.general.string = …` → `PBErrorDomain Code=10/11` (раннер не
  на экране). Текст «от другого приложения» кладётся через Safari: набрать в адресной строке, `tap(withNumberOfTaps: 3,
  numberOfTouches: 1)`, пункт меню «Скопировать» (`safari.menuItems` или `buttons`). Только фиктивные ссылки
  (`example.invalid`): буфер общий и может уйти на Mac.
- **`XCUIDevice.shared.press(.home)` и `springboard.activate()` не всегда уводят приложение в фон** — `app.state`
  остаётся `runningForeground`. Надёжно — открыть другое приложение: `XCUIApplication(bundleIdentifier:
  "com.apple.mobilesafari").activate()`.
- **Записи самого приложения (`NSLog`, `Logger`) по кабелю не видны** — в `idevicesyslog` только системные
  подсистемы процесса. Что происходит внутри: в отладочной сборке писать отметки в `Documents/<файл>` и забирать
  `xcrun devicectl device copy from --domain-type appDataContainer --domain-identifier <bundle id> --source
  Documents/<файл> --destination <путь>`; список файлов — `devicectl device info files … --subdirectory tmp`.
- Ссылка `scheme://` из Safari: набрать адрес с `\n`, потом `safari.buttons["Открыть"]`; адресная строка Safari внизу,
  `safari.textFields.firstMatch`.
- Системный запрос (камера и т. п.) — в `XCUIApplication(bundleIdentifier: "com.apple.springboard").alerts`.
  Снимки с живой камерой не делать: в кадре комната владельца.
- Вкладки SwiftUI помнят открытый экран между запусками — перед проверкой корня нажать «назад» до конца.
- Свайп от края: `coordinate(dx: 0.01) → press(forDuration: 0.05, thenDragTo: dx 0.8)`. На кнопке-строке он
  срабатывает как нажатие — проверять и по строке, и по пустому месту.
- Синтетическое нажатие на системную `PasteButton` iOS принимает — вставка проверяется тестом.

## Какие ошибки кода так нашлись

- **Обработчик UIKit-метода не на главной очереди + Swift 6 = падение.** Замыкание, созданное внутри `@MainActor`
  функции, наследует её изоляцию; если UIKit зовёт его на своей очереди (`UIPasteboard.detectPatterns` →
  `com.apple.UIKit.pasteboard.completion-queue`), проверка изоляции бьёт `EXC_BREAKPOINT`
  (`dispatch_assert_queue_fail` ← `_swift_task_checkIsolatedSwift`). Лечение — `{ @Sendable result in … }`.
  Компилятор не предупреждает, падает только на устройстве и только когда путь срабатывает (в буфере текст).
- **`.refreshable` отменяет свою задачу, как только обновление меняет состояние экрана** — сетевой код с
  `Task.checkCancellation()` падает до запроса, жест «потянуть вниз» каждый раз даёт ошибку. Проверено отметками в
  файл: `cancelled=false` на входе, `cancelled=true` после первой смены состояния. Лечение —
  `.refreshable { await Task { await refresh() }.value }`; кнопка с тем же кодом не страдает.
