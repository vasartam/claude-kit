// node chrome/claude-groups-cleaner/test_logic.mjs
import assert from 'node:assert/strict';
import { groupsToClose, isClaudeGroup, isBusy } from './logic.js';

for (const t of ['Claude', '✅Claude', '✅ Claude', '⌛Claude', '🔔Claude', 'Claude (MCP)', '✅Claude (MCP)']) {
  assert.ok(isClaudeGroup(t), t);
}
for (const t of ['SEO', 'Claude docs', 'My Claude', '', undefined]) {
  assert.ok(!isClaudeGroup(t), String(t));
}
assert.ok(isBusy('⌛Claude') && isBusy('🔔 Claude') && !isBusy('✅Claude'));

const H = 3600_000;
const now = 10 * H;
const groups = [
  { id: 1, title: '✅Claude', hasActiveTab: false }, // давно затихла — закрыть
  { id: 2, title: '✅Claude', hasActiveTab: false }, // недавно — оставить
  { id: 3, title: '⌛Claude', hasActiveTab: false }, // в работе — оставить
  { id: 4, title: 'SEO', hasActiveTab: false }, // не наша — оставить
  { id: 5, title: 'Claude', hasActiveTab: true }, // владелец смотрит — оставить
  { id: 6, title: 'Claude (MCP)', hasActiveTab: false }, // давно — закрыть
  { id: 7, title: 'Claude', hasActiveTab: false }, // нет записи = только что увидели — оставить
];
const last = { 1: now - 3 * H, 2: now - 1 * H, 3: now - 5 * H, 4: now - 9 * H, 5: now - 5 * H, 6: now - 2 * H };
assert.deepEqual(groupsToClose(groups, last, now, { idleMs: 2 * H }), [1, 6]);
// Кнопка: порог 15 минут, текущая (6) остаётся.
assert.deepEqual(groupsToClose(groups, last, now, { idleMs: 15 * 60_000, keepGroupId: 6 }), [1, 2]);

console.log('ok');
