// Хуки набора (Python) не видят то, что знает движок: порог автосжатия, точные токены и окно, лимиты
// 5ч/7д с моментом сброса, модель и effort. Мод пишет это в <claude>/session-status/<sid>.state.json
// на session.measure и turn.step основной сессии — одним и тем же в терминале и в Desktop. Формат и
// правила чтения — hooks/session_state.py (read_mod). Хуки без файла работают по транскрипту,
// поэтому каждая ошибка здесь — тихий пропуск: мод ничего не ломает и не блокирует.
import type { EngineInterface, Register, SessionMeasureInput, SessionRateLimit, TurnStepInput } from 'claude-code'

type Limit = { used_percentage: number; resets_at?: number; at: number }
type Context = {
  tokens?: number
  window?: number
  percent?: number
  auto_compact_at?: number | null
  auto_compact_source?: string
  auto_compact_enabled?: boolean
  at: number
}
// Порог автосжатия, узнанный у движка: верен, пока не сменились модель и окно.
type Threshold = { window: number; enabled: boolean; at?: number; source?: string }
type Target = { sid: string; path: string }

// Память мода: одна сессия. Новая сессия (session.start, смена id после /clear) начинает с пустой.
type Memory = {
  sid?: string
  context?: Context
  limits: { five_hour?: Limit; seven_day?: Limit }
  model?: { id: string; at: number }
  effort?: { level: string | number; at: number }
  threshold?: Threshold
  tries: number // сколько раз за модель и окно просили порог у движка
  written: string // последний записанный текст без времён: без изменений файл не трогаем
}

const MAX_TRIES = 3

const fresh = (sid?: string): Memory => ({ sid, limits: {}, tries: 0, written: '' })

let mem: Memory = fresh()
let claudeDir: string | undefined

const nowSec = () => Date.now() / 1000

const trimSep = (path: string) => path.replace(/[\\/]+$/, '')

// Как safe_sid в session_state.py (для UUID совпадает с сырым id).
const safeSid = (sid: string) => sid.replace(/[^A-Za-z0-9_-]/g, '_')

// Каталог <claude>: CLAUDE_CONFIG_DIR, иначе (USERPROFILE или HOME)/.claude — как Path.home() в Python:
// на Windows он берёт USERPROFILE, даже если HOME задан (git-bash).
// Имена переменных — строковые литералы: так требует движок.
async function findClaudeDir($: EngineInterface): Promise<string | undefined> {
  if (claudeDir) return claudeDir
  const config = await $.env.get('CLAUDE_CONFIG_DIR')
  if (config) return (claudeDir = trimSep(config))
  const home = (await $.env.get('USERPROFILE')) || (await $.env.get('HOME'))
  if (home) return (claudeDir = `${trimSep(home)}/.claude`)
  return undefined
}

// Куда писать; нет каталога или id сессии — мод ничего не делает. Смена id (/clear) обнуляет память.
async function begin($: EngineInterface): Promise<Target | undefined> {
  const dir = await findClaudeDir($)
  const sid = await $.session.id()
  if (!dir || !sid) return undefined
  if (mem.sid !== sid) mem = fresh(sid)
  return { sid, path: `${dir}/session-status/${safeSid(sid)}.state.json` }
}

// Пишет файл, только если изменилось что-то кроме времён (`ts` и `at`): turn.step идёт на каждый запрос.
// Поле, которого движок не дал, не попадает в файл: ноль вместо «нет данных» хуки приняли бы за факт.
async function flush($: EngineInterface, target: Target): Promise<void> {
  const limits = mem.limits.five_hour || mem.limits.seven_day ? mem.limits : undefined
  const body = { context: mem.context, rate_limits: limits, model: mem.model, effort: mem.effort }
  if (!body.context && !body.rate_limits && !body.model && !body.effort) return
  const text = JSON.stringify(body, (key, value) => (key === 'at' ? undefined : value))
  if (text === mem.written) return
  try {
    await $.fs.write(target.path, JSON.stringify({ v: 1, session_id: target.sid, ts: nowSec(), ...body }))
    mem.written = text
  } catch {
    // Не записалось — повторим на следующем событии. Читатель переживает и недописанный файл.
  }
}

// Окна кроме five_hour и seven_day (spend_limit шлюза) не пишутся. Показание прошлого замера остаётся,
// пока движок не дал новое: оно нижняя граница, пока не прошёл resets_at (проверяет читатель).
function takeLimits(list: readonly SessionRateLimit[] | undefined, now: number): void {
  for (const r of list ?? []) {
    if (r.kind !== 'five_hour' && r.kind !== 'seven_day') continue
    if (typeof r.percentUsed !== 'number') continue
    const limit: Limit = { used_percentage: r.percentUsed, at: now }
    const ms = r.resetsAt ? Date.parse(r.resetsAt) : NaN
    if (Number.isFinite(ms)) limit.resets_at = Math.round(ms / 1000)
    mem.limits[r.kind] = limit
  }
}

// Порог автосжатия считает движок (его не знает ни статусная строка, ни транскрипт). Режим summary
// запросов не шлёт, но считает категории, поэтому зовём на первом замере, после смены модели и окна,
// не чаще MAX_TRIES раз. Ошибка или нет поля — порога в файле нет, читатель считает его формулой.
async function readThreshold($: EngineInterface, window: number): Promise<void> {
  if (mem.threshold && mem.threshold.window !== window) {
    mem.threshold = undefined
    mem.tries = 0
  }
  if (mem.threshold || mem.tries >= MAX_TRIES) return
  mem.tries += 1
  try {
    const usage = await $.session.usage({ breakdown: 'summary', columns: 80 })
    const b = usage.context.breakdown
    if (!b) return
    if (b.isAutoCompactEnabled === false) {
      mem.threshold = { window, enabled: false, source: b.autocompactSource }
    } else if (typeof b.autoCompactThreshold === 'number' && b.autoCompactThreshold > 0) {
      mem.threshold = { window, enabled: true, at: b.autoCompactThreshold, source: b.autocompactSource }
    }
  } catch {
    // см. выше
  }
}

function buildContext(c: SessionMeasureInput['context'], now: number): Context {
  const context: Context = { at: now }
  // До первого ответа токенов нет: поля не пишем, а не нули.
  if (typeof c.tokens === 'number' && c.tokens > 0) {
    context.tokens = c.tokens
    if (typeof c.percent === 'number') context.percent = c.percent
  }
  if (typeof c.window === 'number' && c.window > 0) context.window = c.window
  const th = mem.threshold
  if (th && th.window === c.window) {
    context.auto_compact_at = th.enabled ? (th.at ?? null) : null
    if (th.source) context.auto_compact_source = th.source
    context.auto_compact_enabled = th.enabled
  }
  return context
}

async function onStart($: EngineInterface): Promise<void> {
  mem = fresh()
  const target = await begin($)
  if (!target) return
  try {
    // На старте движок знает только окно модели: лимитов и токенов ещё нет.
    const usage = await $.session.usage()
    takeLimits(usage.rateLimits, nowSec())
    const window = usage.context?.window
    if (typeof window === 'number' && window > 0) mem.context = { window, at: nowSec() }
  } catch {
    // нет данных — файл появится на первом замере
  }
  await flush($, target)
}

async function onMeasure($: EngineInterface, e: SessionMeasureInput): Promise<void> {
  const target = await begin($)
  if (!target) return
  const now = nowSec()
  await readThreshold($, e.context.window)
  mem.context = buildContext(e.context, now)
  takeLimits(e.rateLimits, now)
  await flush($, target)
}

// Только основная сессия (без agentId): хуки следят за ней, а не за сабагентами. Время `at` у модели
// и effort — когда значение сменилось: транскрипт подтвердит ту же модель позже, а /model мод
// увидит на следующем запросе.
async function onStep($: EngineInterface, e: TurnStepInput): Promise<void> {
  const target = await begin($)
  if (!target) return
  const now = nowSec()
  let changed = false
  if (e.model && mem.model?.id !== e.model) {
    if (mem.model) {
      mem.threshold = undefined // порог зависит от модели: на ближайшем замере спросим заново
      mem.tries = 0
    }
    mem.model = { id: e.model, at: now }
    changed = true
  }
  if (e.effort === undefined ? mem.effort !== undefined : mem.effort?.level !== e.effort) {
    mem.effort = e.effort === undefined ? undefined : { level: e.effort, at: now }
    changed = true
  }
  if (changed) await flush($, target)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await onStart($)
    } catch {
      // мод не мешает сессии
    }
    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    try {
      await onMeasure($, e)
    } catch {
      // см. выше
    }
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    if (!e.agentId) {
      try {
        await onStep($, e)
      } catch {
        // см. выше
      }
    }
    return yield* next(e)
  })
}
