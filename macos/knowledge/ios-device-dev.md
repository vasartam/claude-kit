# iOS-приложение на свой iPhone с Mac

Как собрать и поставить своё приложение на iPhone по кабелю, управлять им с Mac и где грабли.
Родилось 25.10.2026: VPN-клиент (SwiftUI + Network Extension, проект на XcodeGen), Xcode 27.1, iPhone 13 на iOS 17.5.

## Бесплатный Apple ID или платный аккаунт

- Бесплатный Apple ID (в Xcode — «Personal Team») ставит приложение по кабелю, но без части прав. По таблице Apple
  «Supported capabilities (iOS)» бесплатным недоступны **Network Extensions, Personal VPN, Associated Domains**
  (и Push, iCloud). Доступны App Groups и Keychain sharing. VPN-туннель так не проверить ничем, в симуляторе
  тоже: провайдеры NE там не работают.
- Без права NE система отказывает сразу, без диалога: `NEConfigurationErrorDomain Code=10 "permission denied"`.
- Ограничения бесплатного: профиль живёт 7 дней (потом пересобрать и поставить), до 3 приложений на устройстве,
  до 10 App ID за 7 дней (приложение с расширением — 2).
- Сборка для бесплатного: свой bundle id и App Group (не занимать идентификаторы будущего платного аккаунта),
  убрать из entitlements NE и Associated Domains, из зависимостей приложения — цель расширения.
- Чужая платная команда: добавить участника с правом подписи можно только у аккаунта **организации**; у личного
  аккаунта — только TestFlight или ad hoc по UDID, который подписывает владелец.
- Анкета «Enroll in the Apple Developer Program» — это платная запись, для бесплатной установки не нужна.

## Подготовка

- Xcode без `sudo xcode-select`: `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` в окружении команд.
  Лицензию принимает владелец (пароль), проще — открыть Xcode.
- Team ID бесплатной команды — `defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier` (появляется,
  когда Xcode загрузил команды). Сертификат можно создать в Settings → Apple Accounts → Manage Certificates.
- iPhone: сопряжение `xcrun devicectl manage pair --device <UDID>` (диалог «Доверять» на телефоне), затем
  «Режим разработчика» в Настройки → Конфиденциальность и безопасность (появляется после сопряжения), перезагрузка.
  После перезагрузки iPhone не даёт данных по USB, пока его не разблокируют — `devicectl` пишет `unavailable`.
- Первый запуск: Настройки → Основные → VPN и управление устройством → «Доверять». Нужен **интернет на
  телефоне** (проверка у `ppq.apple.com`), иначе «невозможно проверить приложение».

## Сборка и управление с Mac

```sh
xcodebuild -project X.xcodeproj -scheme X -destination 'platform=iOS,id=<UDID>' \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration build
xcrun devicectl device install app --device <UDID> path/X.app
xcrun devicectl device process launch --device <UDID> [--payload-url 'scheme://…'] <bundle id>
xcrun devicectl device capture screenshot --device <UDID> --destination shot.png   # Xcode 27+
```

- `--payload-url` открывает в приложении диплинк — так можно передать данные без тапов.
- Снимок экрана: `idevicescreenshot` на iOS 17 не работает (cryptex DDI), `devicectl … capture screenshot` — да.
- Журнал и отчёты о падениях — libimobiledevice (`brew install libimobiledevice`): `idevicesyslog -u <UDID> -p <процесс>`,
  `idevicecrashreport -u <UDID> -k -f <процесс> <папка>`. Им нужно **классическое** сопряжение (`idevicepair pair`
  + «Доверять» на телефоне): сопряжение `devicectl` им не подходит, ошибка `Invalid HostID (-21)`.
- Тапать по экрану с Mac нечем (кроме своего UI-теста XCUITest) — тапает человек, агент снимает экран.
- Автоблокировку телефона на время тестов — «Никогда» (Экран и яркость).

## Грабли

- **XcodeGen 2.46 стирает entitlements.** `entitlements: path:` без `properties:` — `xcodegen generate`
  перезаписывает файл пустым `<dict/>`, подписанная сборка уходит без прав, а неподписанная сборка CI этого не видит.
  Ключи — в `properties:`.
- **Swift 6.4 (Xcode 27.1):** override `NEPacketTunnelProvider.startTunnel(options:) async` — ошибка «non-Sendable
  parameter type '[String : NSObject]?' cannot be sent … into @concurrent override». Лечится
  `nonisolated(nonsending)` на override; для компиляторов до 6.2 — второй вариант под `#if compiler(>=6.2)`/`#else`.
  Атрибут нужен на функции с телом: вспомогательная `@concurrent`-функция снова «отправляет» `self`.
- **«Failed to retrieve development teams» / вечное «Loading teams…» в России:** не открывается
  `appstoreconnect.apple.com` — DNS отдаёт узел Akamai, до которого провайдер не пропускает TCP (пинг идёт).
  Проверка: `curl -4 -m 20 -w '%{http_code} %{time_total}\n' -o /dev/null https://appstoreconnect.apple.com/`
  и тот же адрес через `dig @1.1.1.1`. Лечение: маршрут этого домена через VPN роутера или временно строка в
  `/etc/hosts` с адресом от 1.1.1.1. Если DNS Mac идёт через локальный резолвер VPN-клиента, правка DNS на роутере
  не поможет — нужен маршрут по домену (SNI) или по IP.
