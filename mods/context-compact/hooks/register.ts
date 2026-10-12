// Сжатие контекста там, где нет панели herdr (приложение, терминал вне herdr): владелец не должен набирать
// /compact сам (просьба владельца 04.10.2026). context-guard после раскладки знаний просит модель выполнить
// отдельной командой `echo context-compact [--resend <файл>] -- <инструкция>`; мод видит эту команду, после
// конца хода запускает /compact с инструкцией ($.command.run — как набранный владельцем; $.session.compact
// в приложении недоступен) и, если сжатие прошло, присылает запрос, пришедший до сжатия, или продолжение —
// как claude-compact в herdr. Файл-признак <claude>/context-guard/<sid>.compact-mod говорит context-guard,
// что мод есть. Любая ошибка — тихий пропуск и признак ok: false: хук вернётся к просьбе владельцу.
import type { EngineInterface, Register } from 'claude-code'
import { continuation, parseSignal, type Job } from './signal'

const DELAY_MS = 1500 // пауза после turn.complete: сжатие во время хода движок отклоняет (проба 3)

const trimSep = (path: string) => path.replace(/[\\/]+$/, '')

// Как safe_sid в hooks/session_state.py (для UUID совпадает с сырым id).
const safeSid = (sid: string) => sid.replace(/[^A-Za-z0-9_-]/g, '_')

let claudeDir: string | undefined
let headless: boolean | undefined
let job: Job | undefined // сигнал этого хода
let compactedAt = 0 // когда движок последний раз сжал контекст (session.compact без skip)

// Каталог <claude> — как в моде session-state: CLAUDE_CONFIG_DIR, иначе (USERPROFILE или HOME)/.claude.
async function findClaudeDir($: EngineInterface): Promise<string | undefined> {
  if (claudeDir) return claudeDir
  const config = await $.env.get('CLAUDE_CONFIG_DIR')
  if (config) return (claudeDir = trimSep(config))
  const home = (await $.env.get('USERPROFILE')) || (await $.env.get('HOME'))
  if (home) return (claudeDir = `${trimSep(home)}/.claude`)
  return undefined
}

// Как в хуках набора: CLAUDE_CODE_ENTRYPOINT sdk-* — сессия без человека, сжимать некому и незачем.
async function isHeadless($: EngineInterface): Promise<boolean> {
  if (headless === undefined) headless = ((await $.env.get('CLAUDE_CODE_ENTRYPOINT')) ?? '').startsWith('sdk')
  return headless
}

async function mark($: EngineInterface, ok: boolean, error?: string): Promise<void> {
  try {
    const sid = await $.session.id()
    const dir = await findClaudeDir($)
    if (!sid || !dir) return
    const body = { v: 1, session_id: sid, ts: Date.now() / 1000, ok, ...(error ? { error } : {}) }
    await $.fs.write(`${dir}/context-guard/${safeSid(sid)}.compact-mod`, JSON.stringify(body))
  } catch {
    // мод не мешает сессии
  }
}

async function compact($: EngineInterface, j: Job): Promise<void> {
  const started = Date.now()
  try {
    await $.command.run({ command: 'compact', args: j.instructions })
  } catch (err) {
    await mark($, false, `command.run: ${String(err)}`)
    return
  }
  // Сжатие отменил хук (PreCompact context-guard: раскладка не свежая) — продолжение не нужно, хук сам
  // попросит разложить знания.
  if (compactedAt < started) return
  let request: string | undefined
  if (j.resend) {
    try {
      request = await $.fs.read(j.resend)
    } catch {
      // нет файла — сошлёмся на путь
    }
  }
  try {
    const r = await $.prompt.submit({ text: continuation(j, request) })
    if (r.drop !== undefined) await mark($, false, `prompt.submit: ${String(r.drop)}`)
  } catch (err) {
    await mark($, false, `prompt.submit: ${String(err)}`)
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      job = undefined
      if (!(await isHeadless($))) await mark($, true)
    } catch {
      // см. выше
    }
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (!e.agentId) {
      try {
        const j = parseSignal(String(e.command ?? ''))
        if (j) job = j
      } catch {
        // см. выше
      }
    }
    return next(e)
  })

  on('session.compact', async ($, e, next) => {
    const r = await next(e)
    if (r.skip === undefined) compactedAt = Date.now()
    return r
  })

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId || !job) return r
    const j = job
    job = undefined
    // Прерванный ход или ошибка API — не сжимать: владелец, может быть, остановил модель нарочно.
    if (e.reason !== 'answer') return r
    try {
      if (!(await isHeadless($))) $.clock.after(DELAY_MS, () => void compact($, j))
    } catch {
      // см. выше
    }
    return r
  })
}
