// Статусная строка Claude Code (команда) не знает, какой агент открыт на экране: в её JSON всегда
// основная сессия. Мод это знает — полоса над полем ввода получает view.agentId и перерисовывается
// при переключении — и пишет открытого агента с его моделью и effort в
// ~/.claude/session-status/<session_id>.view.json. Скрипт claude-statusline читает файл и, пока открыт
// сабагент, показывает его модель, effort и контекст. Сама полоса мод не рисует.
import type { EngineInterface, Register } from 'claude-code'

type Step = { model: string; effort?: string | number }

// Модель и effort последнего запроса каждого сабагента, по id.
const steps = new Map<string, Step>()
let viewed: string | undefined
let written = ''

async function publish($: EngineInterface) {
  const home = await $.env.get('HOME')
  const sid = await $.session.id()
  if (!home || !sid) return

  let view: Record<string, unknown> = {}
  if (viewed) {
    const info = (await $.agent.list()).find(a => a.id === viewed)
    view = { agentId: viewed, label: info?.description ?? info?.type, ...steps.get(viewed) }
  }
  const text = JSON.stringify(view)
  if (text === written) return
  written = text
  await $.fs.write(`${home}/.claude/session-status/${sid}.view.json`, text)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    viewed = undefined
    written = ''
    await publish($)
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    if (e.agentId) {
      steps.set(e.agentId, { model: e.model, effort: e.effort })
      if (e.agentId === viewed) await publish($)
    }
    return yield* next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.view.agentId !== viewed) {
      viewed = e.props.view.agentId
      await publish($)
    }
    return next(e)
  })
}
