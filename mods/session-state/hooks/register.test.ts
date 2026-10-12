import { test, expect, mock } from 'claude-code/testing'

const SID = 'a1b2c3d4-0000-4000-8000-abcdefabcdef'

function setup(on: any, env: Record<string, string>, usage: any = {}) {
  mock.env(on, env)
  const writes: Array<{ path: string; json: any }> = []
  let sid = SID
  const calls: any[] = []
  on('session.id', () => ({ value: sid }))
  on('fs.write', (_$: any, e: any) => {
    // на Windows движок отдаёт путь с диском и обратными слэшами (C:\\home\\u\\.claude\\…)
    writes.push({ path: e.path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/'), json: JSON.parse(e.text) })
    return { value: undefined }
  })
  on('session.start', (_$: any, e: any) => ({ cwd: e.cwd }))
  on('session.measure', (_$: any, e: any) => ({ changed: e.changed }))
  on('turn.step', async function* (_$: any, e: any) {
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [] }
  })
  on('session.usage', (_$: any, e: any) => {
    calls.push(e)
    if (usage.fail && e.breakdown) throw new Error('nope')
    return { value: {
      startedAt: 0,
      context: {
        window: 1000000,
        ...(e.breakdown ? { breakdown: usage.breakdown } : {}),
      },
      rateLimits: [],
    } }
  })
  return { writes, calls, setSid: (s: string) => (sid = s) }
}

const breakdown = { isAutoCompactEnabled: true, autoCompactThreshold: 967000, autocompactSource: 'model-default' }

test('start пишет окно; measure — контекст, лимиты и порог', async ($, on) => {
  const t = setup(on, { HOME: '/home/u' }, { breakdown })
  await ($ as any).session.start({ cwd: '/work', surface: 'terminal' })
  expect(t.writes.length).toBe(1)
  expect(t.writes[0]!.path).toBe(`/home/u/.claude/session-status/${SID}.state.json`)
  expect(t.writes[0]!.json.context.window).toBe(1000000)
  expect(t.writes[0]!.json.context.tokens).toBeUndefined()

  await ($ as any).session.measure({
    context: { tokens: 272714, window: 1000000, percent: 27 },
    rateLimits: [
      { kind: 'five_hour', percentUsed: 3, resetsAt: '2026-10-03T20:00:00.400Z' },
      { kind: 'seven_day', percentUsed: 4.5, resetsAt: '2026-10-08T10:00:00Z' },
      { kind: 'spend_limit', percentUsed: 90 },
    ],
    changed: ['context', 'rateLimits'],
  })
  expect(t.writes.length).toBe(2)
  const j = t.writes[1]!.json
  expect(j.v).toBe(1)
  expect(j.session_id).toBe(SID)
  expect(typeof j.ts).toBe('number')
  expect(j.context.tokens).toBe(272714)
  expect(j.context.percent).toBe(27)
  expect(j.context.auto_compact_at).toBe(967000)
  expect(j.context.auto_compact_source).toBe('model-default')
  expect(j.context.auto_compact_enabled).toBe(true)
  expect(j.rate_limits.five_hour.used_percentage).toBe(3)
  expect(j.rate_limits.five_hour.resets_at).toBe(Math.round(Date.parse('2026-10-03T20:00:00.400Z') / 1000))
  expect(Number.isInteger(j.rate_limits.five_hour.resets_at)).toBe(true)
  expect(j.rate_limits.seven_day.used_percentage).toBe(4.5)
  expect(j.rate_limits.spend_limit).toBeUndefined()
  // порог спрошен один раз с summary
  expect(t.calls.filter((c: any) => c.breakdown === 'summary').length).toBe(1)

  // тот же замер — файл не пишется, порог не спрашивается
  await ($ as any).session.measure({
    context: { tokens: 272714, window: 1000000, percent: 27 },
    rateLimits: [],
    changed: ['context'],
  })
  expect(t.writes.length).toBe(2)
  expect(t.calls.filter((c: any) => c.breakdown === 'summary').length).toBe(1)
})

test('turn.step: модель и effort основной сессии; сабагент и повтор — без записи', async ($, on) => {
  // Как Path.home() в Python на Windows: USERPROFILE главнее HOME (git-bash задаёт оба).
  const t = setup(on, { USERPROFILE: '/win/x/', HOME: '/c/Users/x' }, { breakdown })
  const step = async (e: any) => {
    for await (const _ of ($ as any).turn.step({ turnId: 't', index: 0, messageCount: 1, ...e })) void _
  }
  await step({ model: 'claude-opus-5-5', effort: 'high' })
  expect(t.writes.length).toBe(1)
  expect(t.writes[0]!.path).toBe(`/win/x/.claude/session-status/${SID}.state.json`)
  expect(t.writes[0]!.json.model.id).toBe('claude-opus-5-5')
  expect(t.writes[0]!.json.effort.level).toBe('high')
  expect(t.writes[0]!.json.context).toBeUndefined()
  await step({ model: 'claude-opus-5-5', effort: 'high' })
  expect(t.writes.length).toBe(1)
  await step({ model: 'claude-haiku-5', effort: 'low', agentId: 'sub1' })
  expect(t.writes.length).toBe(1)
  await step({ model: 'claude-opus-5-5', effort: 'max' })
  expect(t.writes.length).toBe(2)
  expect(t.writes[1]!.json.effort.level).toBe('max')
  await step({ model: 'claude-opus-5-5' })
  expect(t.writes.length).toBe(3)
  expect(t.writes[2]!.json.effort).toBeUndefined()
})

test('смена модели — порог спрашивается заново; ошибка порога — поля нет', async ($, on) => {
  const t = setup(on, { HOME: '/home/u' }, { breakdown })
  const step = async (e: any) => {
    for await (const _ of ($ as any).turn.step({ turnId: 't', index: 0, messageCount: 1, ...e })) void _
  }
  const measure = (tokens: number) =>
    ($ as any).session.measure({ context: { tokens, window: 1000000 }, rateLimits: [], changed: ['context'] })
  await step({ model: 'm1' })
  await measure(100)
  await measure(200)
  expect(t.calls.filter((c: any) => c.breakdown).length).toBe(1)
  await step({ model: 'm2' })
  await measure(300)
  expect(t.calls.filter((c: any) => c.breakdown).length).toBe(2)
})

test('автосжатие выключено — auto_compact_at null; ошибка порога не ломает замер', async ($, on) => {
  const t = setup(on, { HOME: '/home/u' }, { breakdown: { isAutoCompactEnabled: false, autocompactSource: 'settings' } })
  await ($ as any).session.measure({ context: { tokens: 10, window: 200000, percent: 5 }, rateLimits: [], changed: ['context'] })
  const c = t.writes[0]!.json.context
  expect(c.auto_compact_at).toBe(null)
  expect(c.auto_compact_enabled).toBe(false)
})

test('ошибка порога не ломает замер: поля порога нет, остальное пишется; ретраи — не больше трёх', async ($, on) => {
  const t = setup(on, { HOME: '/home/u' }, { fail: true })
  for (let i = 1; i <= 5; i++)
    await ($ as any).session.measure({ context: { tokens: i * 10, window: 200000, percent: 5 }, rateLimits: [], changed: ['context'] })
  expect(t.writes.length).toBe(5)
  expect(t.writes[4]!.json.context.tokens).toBe(50)
  expect(t.writes[4]!.json.context.auto_compact_at).toBeUndefined()
  expect(t.calls.filter((c: any) => c.breakdown).length).toBe(3)
})

test('смена id сессии (/clear) обнуляет память', async ($, on) => {
  const t = setup(on, { CLAUDE_CONFIG_DIR: '/cfg/' }, { breakdown })
  await ($ as any).session.measure({ context: { tokens: 10, window: 200000, percent: 5 }, rateLimits: [{ kind: 'five_hour', percentUsed: 7, resetsAt: '2026-10-03T20:00:00Z' }], changed: ['context'] })
  expect(t.writes[0]!.path).toBe(`/cfg/session-status/${SID}.state.json`)
  t.setSid('new sid/2')
  await ($ as any).session.measure({ context: { window: 200000 }, rateLimits: [], changed: ['context'] })
  expect(t.writes[1]!.path).toBe('/cfg/session-status/new_sid_2.state.json')
  expect(t.writes[1]!.json.rate_limits).toBeUndefined()
  expect(t.writes[1]!.json.context.tokens).toBeUndefined()
  expect(t.writes[1]!.json.context.window).toBe(200000)
})

test('нет ни HOME, ни USERPROFILE, ни CLAUDE_CONFIG_DIR — ничего не пишет; CLAUDE_CONFIG_DIR главнее', async ($, on) => {
  const t = setup(on, {}, { breakdown })
  await ($ as any).session.measure({ context: { tokens: 10, window: 200000 }, rateLimits: [], changed: ['context'] })
  expect(t.writes.length).toBe(0)
})
