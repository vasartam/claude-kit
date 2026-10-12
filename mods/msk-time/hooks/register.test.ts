import { test, expect, mock } from 'claude-code/testing'
import { isStamp, parseClock, stamp, stampTailStart } from './stamp'

const SID = 'a1b2c3d4-0000-4000-8000-abcdefabcdef'
const STAMP = /\n\n_\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} МСК_$/
const MINE = { utc_offset: 3, label: 'МСК', farewell: 'Пока.' }
const MSK = parseClock(MINE, 0)

// config — ключ clock в claude-kit.json; null — файла нет.
function setup(on: any, env: Record<string, string>, chunks: any[] = [], config: any = MINE) {
  mock.env(on, env)
  on('fs.read', () => {
    if (config === null) throw new Error('ENOENT')
    return { value: JSON.stringify({ layers: [], clock: config }) }
  })
  const writes: Array<{ path: string; json: any }> = []
  on('session.id', () => ({ value: SID }))
  on('fs.write', (_$: any, e: any) => {
    // на Windows движок отдаёт путь с диском и обратными слэшами (C:\home\u\.claude\…)
    writes.push({ path: e.path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/'), json: JSON.parse(e.text) })
    return { value: undefined }
  })
  on('session.start', (_$: any, e: any) => ({ cwd: e.cwd }))
  on('turn.step', async function* (_$: any, e: any) {
    yield* chunks
    return { turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn', usage: null }
  })
  return { writes }
}

const text = (index: number, t: string) => ({ kind: 'text', index, text: t })
const stop = (stopReason: string) => ({ kind: 'stop', stopReason, usage: null })

// Прогоняет один запрос через мод: что вышло из хука, по блокам.
async function step($: any, extra: any = {}) {
  const out: any[] = []
  const stream = $.turn.step({ turnId: 't1', index: 0, model: 'm', messageCount: 1, ...extra })
  for await (const c of stream) out.push(c)
  const texts: Record<number, string> = {}
  for (const c of out) if (c.kind === 'text') texts[c.index] = (texts[c.index] ?? '') + c.text
  return { out, texts }
}

test('время — в поясе из настройки, с ведущими нулями, через полночь и Новый год', async () => {
  expect(stamp(Date.UTC(2026, 9, 3, 21, 30, 59), MSK)).toBe('04.10.2026 00:30 МСК')
  expect(stamp(Date.UTC(2026, 11, 31, 21, 5), MSK)).toBe('01.01.2027 00:05 МСК')
  expect(stamp(Date.UTC(2026, 0, 2, 6, 7), MSK)).toBe('02.01.2026 09:07 МСК')
})

test('часы по умолчанию — пояс системы, подпись UTC±N, без прощания', async () => {
  expect(parseClock(undefined, 180)).toEqual({ offsetMin: 180, label: 'UTC+3', farewell: undefined })
  expect(parseClock({ utc_offset: -5, label: 42 }, 0).label).toBe('UTC-5')
  expect(parseClock({ utc_offset: 5.5 }, 0).label).toBe('UTC+5:30')
  expect(parseClock({ utc_offset: 99 }, 60).offsetMin).toBe(60)
})

test('пустая подпись — время без пояса', async () => {
  const bare = parseClock({ utc_offset: 3, label: ' ' }, 0)
  expect(bare.label).toBe('')
  expect(stamp(Date.UTC(2026, 9, 12, 22, 44), bare)).toBe('13.10.2026 01:44')
  expect(isStamp('\n\n_13.10.2026 01:44_\n', bare)).toBe(true)
  expect(isStamp('\n\n_13.10.2026 01:44 МСК_\n', bare)).toBe(false)
  expect(stampTailStart('Готово.\n\n_13.10.2026 01:4', bare)).toBe(7)
})

test('похожий на метку конец текста', async () => {
  expect(stampTailStart('Готово.\n\n_04.10.2026 02:40 МСК_\n', MSK)).toBe(7)
  expect(stampTailStart('Готово.\n\n_04.1', MSK)).toBe(7)
  expect(stampTailStart('Это _важно_', MSK)).toBe(10) // закрывающий «_» — возможно, начало метки
  expect(stampTailStart('Готово.', MSK)).toBe(7)
  expect(isStamp('\n\n_04.10.2026 02:40 МСК_\n', MSK)).toBe(true)
  expect(isStamp('\n\n_04.10.2026 02:4', MSK)).toBe(false)
})

test('конец хода — метка в конце последнего текстового блока, до stop', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Гото'), text(0, 'во.'), stop('end_turn')])
  const { out, texts } = await step($)
  expect(texts[0]).toMatch(/^Готово\./)
  expect(texts[0]).toMatch(STAMP)
  expect(out[out.length - 1].kind).toBe('stop')
})

test('метку модели (по кускам) заменяет метка мода — одна метка', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Готово.\n\n_04.10'), text(0, '.2026 02:40 МСК_\n'), stop('end_turn')])
  const { texts } = await step($)
  expect(texts[0]).toMatch(STAMP)
  expect(texts[0]!.replace(STAMP, '')).toBe('Готово.')
})

test('курсив и пробелы в конце — не метка, текст целиком', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Это _важно_'), text(0, ' и _04.10_\n'), stop('end_turn')])
  const { texts } = await step($)
  expect(texts[0]!.replace(STAMP, '')).toBe('Это _важно_ и _04.10_\n')
})

test('начало метки перед вызовом инструмента — отдаётся как есть', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Шаг _04.'), { kind: 'tool', index: 1, id: 'x', name: 'Bash' }, stop('tool_use')])
  expect((await step($)).texts[0]).toBe('Шаг _04.')
})

test('текст перед вызовом инструмента и ответ без текста — без метки', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Смотрю.'), { kind: 'tool', index: 1, id: 'x', name: 'Bash' }, stop('tool_use')])
  expect((await step($)).texts[0]).toBe('Смотрю.')
})

test('размышление после текста — текст не последний; новый текст — метка у него', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'А'), { kind: 'thinking', index: 1, text: 'хм' }, text(2, 'Б'), stop('end_turn')])
  const { texts } = await step($)
  expect(texts[0]).toBe('А')
  expect(texts[2]).toMatch(STAMP)
})

test('прощание из настройки — без метки', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Всё записано.\n\n**Пока.**'), stop('end_turn')])
  expect((await step($)).texts[0]).toBe('Всё записано.\n\n**Пока.**')
})

test('без claude-kit.json — метка с UTC±N, прощания нет', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'Пока.'), stop('end_turn')], null)
  expect((await step($)).texts[0]).toMatch(/^Пока\.\n\n_\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} UTC[+-]\d+(:\d{2})?_$/)
})

test('сабагент — без метки', async ($, on) => {
  setup(on, { HOME: '/home/u' }, [text(0, 'ok'), stop('end_turn')])
  expect((await step($, { agentId: 'ag1' })).texts[0]).toBe('ok')
})

test('claude -p (CLAUDE_CODE_ENTRYPOINT sdk-*) — без метки и без признака', async ($, on) => {
  const t = setup(on, { HOME: '/home/u', CLAUDE_CODE_ENTRYPOINT: 'sdk-cli' }, [text(0, 'ok'), stop('end_turn')])
  await ($ as any).session.start({ cwd: '/work', surface: 'terminal' })
  expect((await step($)).texts[0]).toBe('ok')
  expect(t.writes.length).toBe(0)
})

test('старт сессии — признак ok по HOME', async ($, on) => {
  const t = setup(on, { HOME: '/home/u' })
  await ($ as any).session.start({ cwd: '/work', surface: 'terminal' })
  expect(t.writes.length).toBe(1)
  expect(t.writes[0]!.path).toBe(`/home/u/.claude/msk-time/${SID}.mod`)
  expect(t.writes[0]!.json).toMatchObject({ v: 1, session_id: SID, ok: true })
})

test('CLAUDE_CONFIG_DIR главнее HOME', async ($, on) => {
  const t = setup(on, { CLAUDE_CONFIG_DIR: '/cfg/', HOME: '/home/u' })
  await ($ as any).session.start({ cwd: '/work', surface: 'terminal' })
  expect(t.writes[0]!.path).toBe(`/cfg/msk-time/${SID}.mod`)
})
