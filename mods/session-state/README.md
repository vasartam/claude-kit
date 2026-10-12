# session-state — мод: состояние сессии для хуков набора

Хуки набора (Python) не видят то, что знает движок: точные токены и окно, порог автосжатия, лимиты
5ч/7д с моментом сброса, модель и effort. Мод пишет это файлом — одинаково в терминале и в приложении;
читает `hooks/session_state.py` (`hooks/README.md`, раздел session_state). Каталог мода вписывает
`scripts/link.py` в `env.CLAUDE_CODE_PLUGIN_DIRS`; мод грузится при старте сессии, правки подхватывает на
лету. Выключить — `exclude: ["session-state"]`: хуки возьмут данные из статусной строки и транскрипта.

## Что и когда пишет

Файл `<claude>/session-status/<sid>.state.json`: `<claude>` — `CLAUDE_CONFIG_DIR`, иначе
`USERPROFILE` или `HOME` + `/.claude` (как `Path.home()` в Python); в `<sid>` всё, кроме
`[A-Za-z0-9_-]`, заменено на `_`. Нет каталога или id — мод ничего не делает.

| Событие | Что пишет |
|---|---|
| `session.start` | память мода — с нуля; окно модели и лимиты, если движок их уже знает |
| `session.measure` | контекст (токены, окно, процент, порог автосжатия) и лимиты |
| `turn.step` основной сессии (без `agentId`) | модель и effort, когда сменились |

- Порог автосжатия — `$.session.usage({breakdown: 'summary'})` (`autoCompactThreshold`): на первом
  замере, после смены модели или окна, не больше 3 попыток. Ошибка — поля нет, Python посчитает порог
  формулой.
- Файл пишется, только если изменилось что-то, кроме времён. Смена id сессии (`/clear`) обнуляет
  память. Сабагентов мод не пишет. Любая ошибка — тихий пропуск: мод ничего не ломает и не блокирует.

## Формат (`v: 1`)

```json
{"v": 1, "session_id": "…", "ts": 1791051234.5,
 "context": {"tokens": 272714, "window": 1000000, "percent": 27, "auto_compact_at": 467000,
             "auto_compact_source": "settings", "auto_compact_enabled": true, "at": 1791051234.5},
 "rate_limits": {"five_hour": {"used_percentage": 3, "resets_at": 1791067200, "at": 1791051234.5},
                 "seven_day": {"used_percentage": 4, "resets_at": 1791396000, "at": 1791051234.5}},
 "model": {"id": "claude-opus-5-5", "at": 1791051200.1},
 "effort": {"level": "high", "at": 1791051200.1}}
```

- Время — секунды эпохи с дробью, `resets_at` — целые секунды. Поля лимитов названы как в
  `rate-limits.json` статусной строки: Python читает оба одним кодом.
- `at` у каждого блока: у контекста и лимитов — время замера, у модели и effort — когда значение
  сменилось.
- Чего движок не дал — поля нет: ноль хуки приняли бы за факт (токенов до первого ответа, лимитов вне
  подписки). Автосжатие выключено — `auto_compact_at: null`, `auto_compact_enabled: false`.
- Лимиты — только `five_hour` и `seven_day`; прошлое показание остаётся, пока движок не дал нового
  (годно до `resets_at`, проверяет читатель).

## Проверка

- Тесты — `hooks/register.test.ts` (события и `fs` — подмены): `claude plugin test` на копии каталога,
  `cp -R mods/session-state <tmp>/ && claude plugin test <tmp>/session-state`.
- Живьём: после промпта в перезапущенной сессии есть `~/.claude/session-status/<sid>.state.json` с
  `context.auto_compact_at`; `python3 hooks/session_state.py <sid>` показывает в `src` значение `mod`.
