import { isBusy } from './logic.js';

const list = document.getElementById('list');
const note = document.getElementById('note');

function ago(ms) {
  const min = Math.round((Date.now() - ms) / 60000);
  return min < 60 ? `${min} мин` : `${Math.floor(min / 60)} ч ${min % 60} мин`;
}

async function currentGroupId() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab?.groupId;
}

async function render() {
  const { groups, recentMs } = await chrome.runtime.sendMessage({ type: 'list' });
  const current = await currentGroupId();
  list.replaceChildren(
    ...groups.map((g) => {
      const li = document.createElement('li');
      const mark = g.id === current ? ' · текущая' : isBusy(g.title) ? ' · в работе' : '';
      li.textContent = `${g.title} (${g.tabs})${mark}`;
      if (isBusy(g.title)) li.className = 'busy';
      const when = document.createElement('span');
      when.textContent = ago(g.lastActive);
      li.append(when);
      return li;
    }),
  );
  if (!groups.length) list.textContent = 'Групп нет.';
  note.textContent = `Не трогает текущую, в работе (⌛/🔔) и живые последние ${recentMs / 60000} мин. ` +
    'Сама закрывает затихшие дольше 2 ч.';
}

document.getElementById('close').addEventListener('click', async () => {
  const { closed } = await chrome.runtime.sendMessage({
    type: 'closeAllButCurrent',
    keepGroupId: await currentGroupId(),
  });
  await render();
  note.textContent = `Закрыто групп: ${closed}. ` + note.textContent;
});

render();
