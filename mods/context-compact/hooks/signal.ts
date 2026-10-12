// Сигнал модели: отдельная команда Bash `echo context-compact [--resend <файл>] -- <инструкция>`.
// Читается сама команда, не вывод echo: кавычки вокруг частей снимаются, обратные слэши путей Windows целы.
export type Job = { instructions: string; resend?: string }

const SIGNAL = /^\s*echo\s+(["']?)context-compact\b([\s\S]*)$/

const unquote = (s: string) => s.trim().replace(/^(["'])([\s\S]*)\1$/, '$2').trim()

export function parseSignal(command: string): Job | undefined {
  const m = SIGNAL.exec(command)
  if (!m) return undefined
  let rest = m[2]!
  // echo "context-compact …": закрывающая кавычка — в конце команды
  if (m[1]) rest = rest.replace(new RegExp(`${m[1]}\\s*$`), '')
  let resend: string | undefined
  const r = /^\s*--resend\s+("[^"]+"|'[^']+'|\S+)/.exec(rest)
  if (r) {
    resend = unquote(r[1]!)
    rest = rest.slice(r[0].length)
  }
  const i = /(^|\s)--(\s|$)/.exec(rest)
  const instructions = unquote(i ? rest.slice(i.index + i[0].length) : rest).replace(/\s*\n\s*/g, ' ')
  return { instructions, ...(resend ? { resend } : {}) }
}

// Что прислать после сжатия — как claude-compact: запрос до сжатия (однострочный — в тексте, иначе —
// ссылкой на файл: многострочный текст движок может принять за вставку) или продолжение по инструкции.
export function continuation(job: Job, request?: string): string {
  const tail = 'и допиши последней строкой ответа строку-цитату: «> контекст сжат, знания разложены».'
  if (!job.resend) return `Контекст сжат (context-compact). Продолжи по инструкции к сжатию: ${job.instructions}`
  const req = (request ?? '').trim()
  if (req && !req.includes('\n') && req.length <= 1500)
    return `Контекст сжат, знания разложены (context-guard). Выполни запрос пользователя, пришедший до сжатия: ${req} — ${tail}`
  return `Контекст сжат, знания разложены (context-guard). Выполни запрос пользователя, пришедший до сжатия, — он в файле ${job.resend} — ${tail}`
}
