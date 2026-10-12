import { test, expect, mock } from 'claude-code/testing'
import { continuation, parseSignal } from './signal'

const SID = 'a1b2c3d4-0000-4000-8000-abcdefabcdef'

test('сигнал: инструкция, --resend, кавычки, путь Windows; чужая команда — нет', async () => {
  expect(parseSignal('echo context-compact -- Знания разложены; дальше X')).toEqual({ instructions: 'Знания разложены; дальше X' })
  expect(parseSignal('echo context-compact --resend /h/.claude/context-guard/s.prompt.md -- "Шла задача Y"'))
    .toEqual({ instructions: 'Шла задача Y', resend: '/h/.claude/context-guard/s.prompt.md' })
  expect(parseSignal('echo "context-compact --resend C:\\Users\\v\\.claude\\context-guard\\s.prompt.md -- итог"'))
    .toEqual({ instructions: 'итог', resend: 'C:\\Users\\v\\.claude\\context-guard\\s.prompt.md' })
  expect(parseSignal('echo context-compact')).toEqual({ instructions: '' })
  expect(parseSignal('echo knowledge-sorted')).toBeUndefined()
  expect(parseSignal('grep context-compact file')).toBeUndefined()
})

test('продолжение: однострочный запрос — в тексте, многострочный — файлом, без --resend — инструкция', async () => {
  const job = { instructions: 'шла задача', resend: '/p.md' }
  expect(continuation(job, 'сделай X\n')).toContain('пришедший до сжатия: сделай X —')
  expect(continuation(job, 'строка 1\nстрока 2')).toContain('он в файле /p.md')
  expect(continuation(job, undefined)).toContain('он в файле /p.md')
  expect(continuation({ instructions: 'шла задача' })).toBe('Контекст сжат (context-compact). Продолжи по инструкции к сжатию: шла задача')
  expect(continuation(job, 'x').startsWith('/')).toBe(false)
})

function setup($: any, on: any, env: Record<string, string>, opts: { skip?: string } = {}) {
  mock.env(on, env)
  const clock = mock.clock(on)
  const writes: Array<{ path: string; json: any }> = []
  const commands: any[] = []
  const submits: any[] = []
  on('session.id', () => ({ value: SID }))
  on('fs.write', (_$: any, e: any) => {
    writes.push({ path: e.path.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/'), json: JSON.parse(e.text) })
    return { value: undefined }
  })
  on('fs.read', () => ({ value: 'сделай X' }))
  on('session.start', (_$: any, e: any) => ({ cwd: e.cwd }))
  on('tool.call', () => ({ result: 'ok' }))
  on('turn.complete', (_$: any, e: any) => ({ text: e.answer }))
  const kept = [{ role: 'user', text: 'сводка', toolUses: [] }]
  on('session.compact', (_$: any, _e: any) => (opts.skip ? { skip: opts.skip } : { messages: kept, tokensBefore: 2, tokensAfter: 1 }))
  on('command.run', async (_$: any, e: any) => {
    commands.push(e)
    await $.session.compact({ trigger: 'manual', instructions: e.args, messages: kept }) // как движок: /compact поднимает session.compact
    return { text: 'Compacted' }
  })
  on('prompt.submit', (_$: any, e: any) => {
    submits.push(e)
    return { text: e.text }
  })
  return { clock, writes, commands, submits }
}

const turn = (extra: any = {}) => ({ answer: 'ok', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer', ...extra })
const bash = (command: string, extra: any = {}) => ({ tool: 'Bash', command, tool_use_id: 'u1', ...extra })

test('старт — признак ok; сигнал → после хода /compact с инструкцией и запрос заново', async ($, on) => {
  const t = setup($, on, { HOME: '/home/u' })
  await ($ as any).session.start({ cwd: '/w', surface: 'desktop' })
  expect(t.writes[0]!.path).toBe(`/home/u/.claude/context-guard/${SID}.compact-mod`)
  expect(t.writes[0]!.json.ok).toBe(true)
  await ($ as any).tool.call(bash('echo context-compact --resend /p.md -- шла задача'))
  await ($ as any).turn.complete(turn())
  expect(t.commands.length).toBe(0) // не во время хода
  await t.clock.advance(2000)
  expect(t.commands).toEqual([expect.objectContaining({ command: 'compact', args: 'шла задача' })])
  expect(t.submits.length).toBe(1)
  expect(t.submits[0].text).toContain('пришедший до сжатия: сделай X')
})

test('без сигнала, прерванный ход, сабагент — не сжимает', async ($, on) => {
  const t = setup($, on, { HOME: '/home/u' })
  await ($ as any).turn.complete(turn())
  await ($ as any).tool.call(bash('echo context-compact -- x'))
  await ($ as any).turn.complete(turn({ reason: 'aborted', isAborted: true }))
  await ($ as any).tool.call(bash('echo context-compact -- x', { agentId: 'ag' }))
  await ($ as any).turn.complete(turn())
  await t.clock.advance(5000)
  expect(t.commands.length).toBe(0)
})

test('сжатие отменил хук — продолжение не шлём', async ($, on) => {
  const t = setup($, on, { HOME: '/home/u' }, { skip: 'context-guard: сначала раскладка' })
  await ($ as any).tool.call(bash('echo context-compact -- x'))
  await ($ as any).turn.complete(turn())
  await t.clock.advance(2000)
  expect(t.commands.length).toBe(1)
  expect(t.submits.length).toBe(0)
})

test('claude -p — ни признака, ни сжатия', async ($, on) => {
  const t = setup($, on, { HOME: '/home/u', CLAUDE_CODE_ENTRYPOINT: 'sdk-cli' })
  await ($ as any).session.start({ cwd: '/w', surface: 'terminal' })
  await ($ as any).tool.call(bash('echo context-compact -- x'))
  await ($ as any).turn.complete(turn())
  await t.clock.advance(2000)
  expect(t.writes.length).toBe(0)
  expect(t.commands.length).toBe(0)
})
