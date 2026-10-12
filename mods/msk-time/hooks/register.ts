// Время ответа ставит программа, а не модель: модель досчитывала его «на глаз» от последней заметки хука
// и уходила в будущее (04.10.2026). Мод дописывает строку `_04.10.2026 00:30 МСК_` (пояс и подпись — из
// ключа clock в <claude>/claude-kit.json, stamp.ts) в конец текста последнего
// ответа хода прямо в потоке turn.step: что выходит из хука, то и показывается, и записывается — в
// терминале и в приложении. Служебная строка $.session.append (type 'system') не годится: в терминале её
// не видно (проба 04.10.2026). Файл-признак <claude>/msk-time/<sid>.mod говорит хуку hooks/msk_time.py,
// что метку от модели требовать не надо. Любая ошибка — тихий пропуск: мод ничего не ломает.
import type { EngineInterface, Register, TurnStepChunk } from 'claude-code'
import { type Clock, isFarewell, isStamp, parseClock, stamp, stampTailStart } from './stamp'

const trimSep = (path: string) => path.replace(/[\\/]+$/, '')

// Как safe_sid в hooks/session_state.py (для UUID совпадает с сырым id).
const safeSid = (sid: string) => sid.replace(/[^A-Za-z0-9_-]/g, '_')

let claudeDir: string | undefined
let headless: boolean | undefined // claude -p, Agent SDK: их вывод читают скрипты — метку не трогаем
let marked = '' // «<sid> ok» или «<sid> fail»: что уже записано в файл-признак
let clock: Clock | undefined

// Каталог <claude> — как в моде session-state: CLAUDE_CONFIG_DIR, иначе (USERPROFILE или HOME)/.claude.
async function findClaudeDir($: EngineInterface): Promise<string | undefined> {
  if (claudeDir) return claudeDir
  const config = await $.env.get('CLAUDE_CONFIG_DIR')
  if (config) return (claudeDir = trimSep(config))
  const home = (await $.env.get('USERPROFILE')) || (await $.env.get('HOME'))
  if (home) return (claudeDir = `${trimSep(home)}/.claude`)
  return undefined
}

// Файл-признак: ok — время ставит мод; fail — что-то сломалось, пусть метку снова пишет модель.
async function mark($: EngineInterface, ok: boolean, error?: string): Promise<void> {
  const sid = await $.session.id()
  if (!sid || marked === `${sid} ${ok ? 'ok' : 'fail'}`) return
  const dir = await findClaudeDir($)
  if (!dir) return
  const body = { v: 1, session_id: sid, ts: Date.now() / 1000, ok, ...(error ? { error } : {}) }
  await $.fs.write(`${dir}/msk-time/${safeSid(sid)}.mod`, JSON.stringify(body))
  marked = `${sid} ${ok ? 'ok' : 'fail'}`
}

// Часы из <claude>/claude-kit.json (ключ clock); нет файла или ключа — пояс системы.
async function loadClock($: EngineInterface): Promise<Clock> {
  if (clock) return clock
  let raw: unknown
  try {
    const dir = await findClaudeDir($)
    if (dir) raw = JSON.parse(await $.fs.read(`${dir}/claude-kit.json`)).clock
  } catch {
    // нет конфига — значения по умолчанию
  }
  return (clock = parseClock(raw, -new Date().getTimezoneOffset()))
}

// Как в hooks/msk_time.py: CLAUDE_CODE_ENTRYPOINT sdk-* — сессия без человека.
async function isHeadless($: EngineInterface): Promise<boolean> {
  if (headless === undefined) headless = ((await $.env.get('CLAUDE_CODE_ENTRYPOINT')) ?? '').startsWith('sdk')
  return headless
}

async function markQuietly($: EngineInterface, ok: boolean, error?: string): Promise<void> {
  try {
    await mark($, ok, error)
  } catch {
    // мод не мешает сессии
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      if (!(await isHeadless($))) await mark($, true)
    } catch {
      // мод не мешает сессии
    }
    return next(e)
  })

  // Последний текстовый блок ответа держим «открытым»: служебные куски движка (конец блока и т. п.) после
  // него придерживаем до следующего куска. Пришёл stop с end_turn — ответ хода окончен: дописываем метку в
  // тот же блок и отпускаем придержанное. Пришёл вызов инструмента или размышление — текст не последний.
  // Конец текста, похожий на метку (stampTailStart), придерживаем тоже: окажется к концу хода меткой
  // модели — выбрасываем, вместо неё встаёт метка мода; иначе отдаём как есть.
  on('turn.step', async function* ($, e, next) {
    if (e.agentId || (await isHeadless($).catch(() => true))) return yield* next(e)
    const c = await loadClock($)
    const stream = next(e)
    let held: TurnStepChunk[] = []
    let last: { index: number; text: string } | undefined
    let tail = ''
    const takeTail = (): TurnStepChunk[] => {
      const out: TurnStepChunk[] = tail && last ? [{ kind: 'text', index: last.index, text: tail }] : []
      tail = ''
      return out
    }
    for (;;) {
      const { value, done } = await stream.next()
      if (done) {
        yield* takeTail()
        yield* held
        return value
      }
      const chunk = value as TurnStepChunk
      if (chunk.kind === 'engine' && last) {
        held.push(chunk)
        continue
      }
      if (chunk.kind === 'stop' && chunk.stopReason === 'end_turn' && last && !isFarewell(last.text, c)) {
        let mine: string | undefined
        try {
          mine = `\n\n_${stamp(Date.now(), c)}_`
        } catch (err) {
          await markQuietly($, false, String(err))
        }
        if (mine !== undefined && isStamp(tail, c)) tail = ''
        yield* takeTail()
        if (mine !== undefined) yield { kind: 'text', index: last.index, text: mine }
      }
      const sameBlock = chunk.kind === 'text' && last?.index === chunk.index
      if (!sameBlock) yield* takeTail()
      yield* held
      held = []
      if (chunk.kind === 'text') {
        if (!sameBlock) last = { index: chunk.index, text: '' }
        last!.text += chunk.text
        const s = tail + chunk.text
        const cut = stampTailStart(s, c)
        tail = s.slice(cut)
        if (cut > 0) yield { ...chunk, text: s.slice(0, cut) }
        continue
      } else if (chunk.kind !== 'engine' && chunk.kind !== 'stop') {
        last = undefined
      }
      yield chunk
    }
  })
}
