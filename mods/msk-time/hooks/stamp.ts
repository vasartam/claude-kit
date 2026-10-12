// Время для строки ответа: «04.10.2026 00:30 МСК». Пояс, подпись и прощание — ключ "clock" в
// <claude>/claude-kit.json, как у хука hooks/msk_time.py: {"utc_offset": 3, "label": "МСК", "farewell": "Пока."}.
// По умолчанию — пояс системы, подпись «UTC+N», прощания нет; "label": "" — время без подписи. Пояс —
// постоянное смещение, без летнего времени.

export type Clock = { offsetMin: number; label: string; farewell?: string }

const pad = (n: number) => String(n).padStart(2, '0')

// Подпись после времени с пробелом перед ней; пустая подпись — ничего.
const tail = (clock: Clock) => (clock.label ? ` ${clock.label}` : '')

function offsetLabel(min: number): string {
  const a = Math.abs(min)
  return `UTC${min >= 0 ? '+' : '-'}${Math.floor(a / 60)}${a % 60 ? `:${pad(a % 60)}` : ''}`
}

// Разбор ключа clock; всё неверное — значение по умолчанию. localOffsetMin — смещение системы в минутах.
export function parseClock(raw: unknown, localOffsetMin: number): Clock {
  const c = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const h = c.utc_offset
  const offsetMin = typeof h === 'number' && h >= -14 && h <= 14 ? Math.round(h * 60) : localOffsetMin
  const label = typeof c.label === 'string' ? c.label.trim() : offsetLabel(offsetMin)
  const farewell = typeof c.farewell === 'string' && c.farewell.trim() ? c.farewell.trim() : undefined
  return { offsetMin, label, farewell }
}

export function stamp(ms: number, clock: Clock): string {
  const d = new Date(ms + clock.offsetMin * 60000)
  const day = `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`
  return `${day} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${tail(clock)}`
}

// Ответ кончается словом прощания (так кончается сессия) — метка не нужна.
export function isFarewell(text: string, clock: Clock): boolean {
  if (!clock.farewell) return false
  const lines = text.trim().split('\n')
  return lines[lines.length - 1]!.trim().replace(/^[*_ ]+|[*_ ]+$/g, '') === clock.farewell
}

// Метку модель иногда пишет и сама, подражая меткам мода в прошлых ответах (04.10.2026 после сжатия:
// две метки подряд, своя — с неверной минутой). Шаблон её строки, D — цифра.
const template = (clock: Clock) => `_DD.DD.DDDD DD:DD${tail(clock)}_`

// s — начало строки метки или целая метка с пробелами после.
function fits(s: string, tpl: string): boolean {
  for (let k = 0; k < s.length; k++) {
    const c = s[k]!
    const t = tpl[k]
    if (t === undefined ? !/\s/.test(c) : t === 'D' ? !/\d/.test(c) : c !== t) return false
  }
  return true
}

// С какого места конец текста может оказаться меткой (целой или недописанной) вместе с пробелами перед
// ней; пробелы в конце тоже — за ними может прийти метка. Нечего придерживать — text.length.
export function stampTailStart(text: string, clock: Clock): number {
  const tpl = template(clock)
  let start = text.length
  for (let j = Math.max(0, text.length - tpl.length - 16); j < text.length; j++) {
    if (text[j] === '_' && fits(text.slice(j), tpl)) {
      start = j
      break
    }
  }
  while (start > 0 && /\s/.test(text[start - 1]!)) start--
  return start
}

export function isStamp(s: string, clock: Clock): boolean {
  const t = s.trim()
  const tpl = template(clock)
  return t.length === tpl.length && fits(t, tpl)
}
