// Чистая логика уборщика: без chrome.*, чтобы проверять в node (test_logic.mjs).

// Группы, которые заводит расширение Claude in Chrome: «Claude», «Claude (MCP)»,
// с приставкой состояния ⌛ (идёт работа), 🔔 (ждёт разрешения), ✅ (затихла).
const CLAUDE_TITLE = /^(⌛|🔔|✅)?\s*Claude( \(MCP\))?$/u;
// Пока группа в работе или ждёт разрешения, её не трогаем, сколько бы она ни молчала.
const BUSY_PREFIX = /^(⌛|🔔)/u;

export const IDLE_MS = 2 * 60 * 60 * 1000; // автоуборка: тишина дольше 2 часов
export const RECENT_MS = 15 * 60 * 1000; // кнопка не трогает группы живее 15 минут

export function isClaudeGroup(title) {
  return CLAUDE_TITLE.test((title || '').trim());
}

export function isBusy(title) {
  return BUSY_PREFIX.test((title || '').trim());
}

// groups: [{id, title, hasActiveTab}] — hasActiveTab: вкладка группы сейчас выбрана в своём окне.
// lastActive: {groupId: ms}. Возвращает id групп «Claude», которые пора закрыть.
export function groupsToClose(groups, lastActive, now, { idleMs, keepGroupId = null }) {
  return groups
    .filter((g) => isClaudeGroup(g.title))
    .filter((g) => !isBusy(g.title))
    .filter((g) => !g.hasActiveTab)
    .filter((g) => g.id !== keepGroupId)
    .filter((g) => now - (lastActive[g.id] ?? now) >= idleMs)
    .map((g) => g.id);
}
