// Расход токенов по всем сессиям Claude Code за период:
//   node scripts/token-usage.js [--root <папка projects>] [с] [по]
// Даты ISO (2026-09-19T16:45:00Z), по умолчанию — последние 7 дней. Журналы — --root, иначе
// $CLAUDE_CONFIG_DIR/projects, иначе ~/.claude/projects.
// Цена: ввод 1x, запись в кэш 2x, чтение из кэша 0.1x, вывод 5x.
// Разбивка по группам внутри сессии — оценка по объёму текста; итоги — точные из журналов.
const fs = require('fs');
const os = require('os');
const path = require('path');
const args = process.argv.slice(2);
const rootAt = args.indexOf('--root');
const ROOT = rootAt >= 0
  ? args.splice(rootAt, 2)[1]
  : path.join(process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude'), 'projects');
const SINCE = new Date(args[0] || Date.now() - 7 * 864e5);
const UNTIL = new Date(args[1] || Date.now());

const CONN = {
  '1a59c906-04da-521d-bda7-7f71b9f9e01c': 'Документы Claude',
  '6f616b42-0ed8-571e-823f-ee4aca6b7ce9': 'Графики в чате',
  Claude_Browser: 'Встроенный браузер',
  visualize: 'Графики в чате',
  vultr: 'Vultr (серверы)',
  terminal: 'Терминал приложения',
  'scheduled-tasks': 'Расписание задач',
  'mcp-registry': 'Каталог коннекторов',
};
function cat(name) {
  if (!name) return 'Прочее';
  if (name.startsWith('mcp__claude-in-chrome__')) return 'Claude в Chrome';
  if (name.startsWith('mcp__')) {
    const srv = name.slice(5).split('__')[0];
    if (srv.startsWith('ccd_')) return 'Панели приложения';
    return CONN[srv] || 'Коннектор ' + srv;
  }
  if (['WebSearch', 'WebFetch'].includes(name)) return 'Веб-поиск';
  if (['Read', 'Edit', 'Write', 'Glob', 'Grep', 'NotebookEdit'].includes(name)) return 'Файлы';
  if (['Bash', 'PowerShell', 'Monitor'].includes(name)) return 'Команды в терминале';
  return 'Прочее';
}
const len = (x) => (typeof x === 'string' ? x.length : JSON.stringify(x ?? '').length) / 4;

function walk(d, out = []) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.jsonl') && fs.statSync(p).mtime >= SINCE) out.push(p);
  }
  return out;
}

const total = {};
const add = (k, v) => (total[k] = (total[k] || 0) + v);
const perProject = {};
const subs = [];
let sessions = new Set();
let calls = 0;

for (const f of walk(ROOT)) {
  const isSub = f.includes(path.sep + 'subagents' + path.sep);
  const project = path.relative(ROOT, f).split(path.sep)[0];
  const seen = new Set();
  const toolCat = {};
  let comp = {}; // оценка состава контекста, токены по группам (кроме инструкций)
  let delta = {}; // добавлено после прошлого вызова
  let base = null;
  let fileCost = 0;
  const lines = fs.readFileSync(f, 'utf8').split('\n');
  const put = (k, v) => {
    comp[k] = (comp[k] || 0) + v;
    delta[k] = (delta[k] || 0) + v;
  };
  for (const line of lines) {
    let d;
    try { d = JSON.parse(line); } catch { continue; }
    if (d.isCompactSummary || d.type === 'summary') { comp = {}; delta = {}; }
    const m = d.message;
    if (!m) continue;
    const content = Array.isArray(m.content) ? m.content : [{ type: 'text', text: m.content }];
    if (d.type === 'user') {
      for (const c of content) {
        if (c.type === 'tool_result') put(toolCat[c.tool_use_id] || 'Прочее', len(c.content));
        else {
          const t = typeof c.text === 'string' ? c.text : JSON.stringify(c);
          const instr = d.isMeta || t.includes('<system-reminder>') || t.includes('<command-');
          put(instr ? 'Инструкции Claude' : 'Разговор', t.length / 4);
        }
      }
      continue;
    }
    if (d.type !== 'assistant') continue;
    // содержимое ответа — в контекст следующих вызовов
    for (const c of content) {
      if (c.type === 'tool_use') { toolCat[c.id] = cat(c.name); put(cat(c.name), len(c.input)); }
      else if (c.type === 'text') put('Разговор', len(c.text));
    }
    const u = m.usage;
    if (!u || seen.has(m.id)) continue;
    seen.add(m.id);
    const ts = new Date(d.timestamp);
    if (ts < SINCE || ts > UNTIL) { delta = {}; continue; }
    calls++;
    if (!isSub) sessions.add(d.sessionId);
    const inp = u.input_tokens || 0, cw = u.cache_creation_input_tokens || 0,
      cr = u.cache_read_input_tokens || 0, out = u.output_tokens || 0;
    const T = inp + cw + cr;
    const cost = inp + 2 * cw + 0.1 * cr + 5 * out;
    fileCost += cost;
    const key = isSub ? 'Субагенты' : null;
    if (key) { add(key, cost); delta = {}; continue; }
    if (base === null) base = Math.max(T - Object.values(comp).reduce((a, b) => a + b, 0), 0);
    // состав контекста: инструкции + отслеживаемое, подогнанное к T
    const instr = Math.min(base, T);
    const S = Object.values(comp).reduce((a, b) => a + b, 0);
    const scale = S > 0 ? (T - instr) / S : 0;
    const share = { 'Инструкции Claude': instr };
    for (const [k, v] of Object.entries(comp)) share[k] = (share[k] || 0) + v * scale;
    if (S === 0) share['Инструкции Claude'] = T;
    const spread = (amount, parts) => {
      const s = Object.values(parts).reduce((a, b) => a + b, 0);
      if (s <= 0) return add('Инструкции Claude', amount);
      for (const [k, v] of Object.entries(parts)) add(k, (amount * v) / s);
    };
    // чтение и ввод — пропорционально всему контексту
    spread(inp + 0.1 * cr, share);
    // запись в кэш — сначала новое, остаток (кэш протух) — весь контекст
    const D = Object.values(delta).reduce((a, b) => a + b, 0) * (S > 0 ? scale : 1);
    const wNew = Math.min(cw, D);
    if (wNew > 0) spread(2 * wNew, delta);
    if (cw - wNew > 0) spread(2 * (cw - wNew), share);
    // вывод — туда, что делал этот ответ
    const tools = content.filter((c) => c.type === 'tool_use').map((c) => cat(c.name));
    if (tools.length) for (const t of tools) add(t, (5 * out) / tools.length);
    else add('Разговор', 5 * out);
    delta = {};
  }
  if (fileCost > 0) {
    perProject[project] = (perProject[project] || 0) + fileCost;
    if (isSub) subs.push([fileCost, f.includes(path.sep + 'workflows' + path.sep)]);
  }
}

const grand = Object.values(total).reduce((a, b) => a + b, 0);
console.log('с', SINCE.toISOString(), 'по', UNTIL.toISOString(), 'сессий', sessions.size, 'вызовов', calls, 'итого', Math.round(grand / 1e6 * 10) / 10, 'млн');
for (const [k, v] of Object.entries(total).sort((a, b) => b[1] - a[1]))
  console.log(k.padEnd(24), (v / 1e6).toFixed(2), 'млн', ((100 * v) / grand).toFixed(1) + '%');
subs.sort((a, b) => b[0] - a[0]);
const wf = subs.filter((s) => s[1]);
const wfCost = wf.reduce((a, s) => a + s[0], 0);
console.log('субагентов', subs.length, 'медиана', Math.round((subs[subs.length >> 1]?.[0] || 0) / 1e3), 'тыс', 'макс', Math.round((subs[0]?.[0] || 0) / 1e3), 'тыс');
console.log('из них workflow/ultracode', wf.length, 'шт,', (wfCost / 1e6).toFixed(0), 'млн');
console.log('--- по проектам');
for (const [k, v] of Object.entries(perProject).sort((a, b) => b[1] - a[1]))
  console.log(k.padEnd(60), (v / 1e6).toFixed(2), ((100 * v) / grand).toFixed(1) + '%');
