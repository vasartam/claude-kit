# Куки Chrome на macOS: расшифровка из базы профиля

Когда нужно: HttpOnly-куку (сессию авторизации) со страницы не достать — ни `document.cookie`, ни
сетевыми логами Claude in Chrome. Единственный источник — база кук Chrome, зашифрованная ключом из
связки ключей. Типовая задача — подложить сессию из браузера владельца в `.env` локального
дев-сервера, который проксирует запросы на стенд.

**Значения кук не печатать:** писать сразу туда, где нужны, в вывод — только имена найденных.

## Схема (проверена на Chrome 130+)

1. **Ключ:** пароль `security find-generic-password -wa Chrome -s "Chrome Safe Storage"` →
   PBKDF2-HMAC-SHA1, соль `saltysalt`, 1003 итерации, 16 байт. Первый раз Keychain спросит
   разрешение — «Разрешать всегда» снимает диалог.
2. **База:** `~/Library/Application Support/Google/Chrome/<профиль>/Cookies` (SQLite). Профилей
   может быть несколько (`Default`, `Profile N`) — искать по всем. Живую базу Chrome держит
   заблокированной — работать с копией во временном каталоге.
3. **Запрос:** `SELECT host_key, name, encrypted_value FROM cookies WHERE host_key LIKE ?`. Куку
   могли выставить и на хост, и на родительский домен — перебирать суффиксы (`%a.b.example.com`,
   `%b.example.com`, …), но не общий домен площадки, где живут чужие сайты.
4. **Расшифровка:** значение с префиксом `v10` — AES-128-CBC, IV из 16 пробелов, снять PKCS7;
   Chrome 130+ пишет перед значением 32 байта хеша домена — отрезать. Без префикса — значение
   открытым текстом. Модуль `cryptography` (`python3 -m pip install --user cryptography`).

```python
import hashlib, subprocess
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def chrome_key() -> bytes:
    pw = subprocess.run(["security", "find-generic-password", "-wa", "Chrome",
                         "-s", "Chrome Safe Storage"], capture_output=True, text=True,
                        check=True).stdout.strip()
    return hashlib.pbkdf2_hmac("sha1", pw.encode(), b"saltysalt", 1003, 16)

def decrypt(blob: bytes, key: bytes) -> str:
    if not blob.startswith(b"v10"):
        return blob.decode("utf-8", "replace")
    d = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
    plain = d.update(blob[3:]) + d.finalize()
    if plain and plain[-1] <= 16:
        plain = plain[:-plain[-1]]
    return (plain[32:] if len(plain) > 32 else plain).decode("utf-8", "replace")
```

## Грабли вокруг

- Переменные `.env`, которых в этот раз не нашлось, **затирать**, а не оставлять: иначе прокси
  продолжит слать куку от прошлого стенда, и стенд ответит 401 без видимой причины.
- Дев-сервер читает `.env` только на старте — после обновления кук перезапустить.
- Ни одной сессионной куки не нашлось — владельцу открыть стенд в Chrome и войти; что вообще лежит
  по домену — показать списком имён без значений.

На Windows шифрование кук другое — приём там не проверялся.
