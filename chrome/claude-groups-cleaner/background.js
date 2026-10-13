// Уборщик групп вкладок «Claude»: помнит, когда в каждой группе что-то происходило,
// и закрывает затихшие. Сливать группы нельзя — Claude in Chrome держит группу за
// своей сессией по id, перенос вкладок сломал бы живую сессию.
import { IDLE_MS, RECENT_MS, groupsToClose, isClaudeGroup } from './logic.js';

const KEY = 'lastActive';
const ALARM = 'sweep';

async function load() {
  return (await chrome.storage.local.get(KEY))[KEY] || {};
}

async function touch(groupId) {
  if (groupId === undefined || groupId === chrome.tabGroups.TAB_GROUP_ID_NONE) return;
  const last = await load();
  last[groupId] = Date.now();
  await chrome.storage.local.set({ [KEY]: last });
}

async function touchTab(tabId) {
  try {
    await touch((await chrome.tabs.get(tabId)).groupId);
  } catch {
    // вкладку уже закрыли
  }
}

// Сводка по группам «Claude»: id, заголовок, число вкладок и открыта ли сейчас перед владельцем
// одна из них (выбрана в последнем окне, где он был: у группы в своём окне выбранная вкладка есть всегда).
export async function claudeGroups() {
  const groups = (await chrome.tabGroups.query({})).filter((g) => isClaudeGroup(g.title));
  const focused = await chrome.windows.getLastFocused().catch(() => null);
  return Promise.all(
    groups.map(async (g) => {
      const tabs = await chrome.tabs.query({ groupId: g.id });
      const hasActiveTab = tabs.some((t) => t.active && t.windowId === focused?.id);
      return { id: g.id, title: g.title, tabs: tabs.length, hasActiveTab };
    }),
  );
}

async function closeGroups(ids) {
  for (const id of ids) {
    const tabs = await chrome.tabs.query({ groupId: id });
    // Закрыли последнюю вкладку — Chrome убирает пустую группу сам.
    if (tabs.length) await chrome.tabs.remove(tabs.map((t) => t.id));
  }
  return ids.length;
}

// Новые группы и группы, о которых нет записи (после перезапуска Chrome id меняются), — «живые сейчас»;
// записи об исчезнувших группах — удалить.
async function reconcile() {
  const live = await chrome.tabGroups.query({});
  const last = await load();
  const now = Date.now();
  const next = {};
  for (const g of live) next[g.id] = last[g.id] ?? now;
  await chrome.storage.local.set({ [KEY]: next });
  return next;
}

async function sweep({ idleMs, keepGroupId = null }) {
  const last = await reconcile();
  return closeGroups(groupsToClose(await claudeGroups(), last, Date.now(), { idleMs, keepGroupId }));
}

// Признаки жизни: переход или загрузка во вкладке группы, вкладку выбрали, вкладку добавили
// в группу, у группы сменилась приставка ⌛/🔔/✅ (её ставит Claude in Chrome, когда работает).
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if ('groupId' in change) touch(change.groupId);
  else if (change.url || change.status === 'loading') touch(tab.groupId);
});
chrome.tabs.onActivated.addListener(({ tabId }) => touchTab(tabId));
chrome.tabs.onCreated.addListener((tab) => touch(tab.groupId));
chrome.tabGroups.onCreated.addListener((g) => touch(g.id));
chrome.tabGroups.onUpdated.addListener((g) => touch(g.id));

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === ALARM) sweep({ idleMs: IDLE_MS });
});

function init() {
  chrome.alarms.create(ALARM, { periodInMinutes: 10 });
  reconcile();
}
chrome.runtime.onInstalled.addListener(init);
chrome.runtime.onStartup.addListener(init);

// Кнопка в окошке расширения: закрыть все группы «Claude», кроме текущей и живых последние 15 минут.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.type === 'list') {
    Promise.all([claudeGroups(), reconcile()]).then(([groups, last]) =>
      reply({ groups: groups.map((g) => ({ ...g, lastActive: last[g.id] })), recentMs: RECENT_MS }),
    );
    return true;
  }
  if (msg?.type === 'closeAllButCurrent') {
    sweep({ idleMs: RECENT_MS, keepGroupId: msg.keepGroupId }).then((closed) => reply({ closed }));
    return true;
  }
  return false;
});
