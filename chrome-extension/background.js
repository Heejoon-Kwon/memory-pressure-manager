/* Tab priorities are tied to the tab and its exact URL; default is protected. */
const HOST = 'local.probe.tabs';
const ALARM = 'probe-memory';

async function settings() {
  const data = await chrome.storage.session.get('tabPriorities');
  return data.tabPriorities || {};
}
async function inspectTabs() {
  const [tabs, priorities] = await Promise.all([chrome.tabs.query({}), settings()]);
  return tabs.map(tab => ({tab, setting: priorities[tab.id]}));
}
function eligible({tab, setting}) {
  return !!setting && Number.isInteger(setting.priority) && setting.priority >= 1 && setting.priority <= 999 &&
    setting.url === tab.url && typeof tab.url === 'string' && /^https?:\/\//i.test(tab.url) &&
    !tab.active && !tab.pinned && !tab.audible && !tab.discarded && !tab.incognito;
}
async function cycle() {
  let entries = (await inspectTabs()).filter(eligible);
  entries.sort((a, b) => b.setting.priority - a.setting.priority ||
    (a.tab.lastAccessed || 0) - (b.tab.lastAccessed || 0));
  if (!entries.length) return;
  const grant = await chrome.runtime.sendNativeMessage(HOST, {type: 'claim'});
  if (!grant.granted) return;
  // Recheck after IPC: a user could have focused, protected, or navigated the tab.
  entries = (await inspectTabs()).filter(eligible);
  entries.sort((a, b) => b.setting.priority - a.setting.priority ||
    (a.tab.lastAccessed || 0) - (b.tab.lastAccessed || 0));
  if (!entries.length) return;
  try {
    await chrome.tabs.discard(entries[0].tab.id);
    await chrome.runtime.sendNativeMessage(HOST, {type: 'report', result: 'discarded'});
  } catch (_) {
    await chrome.runtime.sendNativeMessage(HOST, {type: 'report', result: 'failed'});
  }
}
async function schedule() {
  await chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
}
chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === ALARM) cycle().catch(() => {});
});
chrome.tabs.onRemoved.addListener(async id => {
  const priorities = await settings();
  if (Object.hasOwn(priorities, id)) {
    delete priorities[id];
    await chrome.storage.session.set({tabPriorities: priorities});
  }
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === 'test-connection') {
    chrome.runtime.sendNativeMessage(HOST, {type: 'status'}).then(respond, error => respond({error: error.message}));
    return true;
  }
});
// Testable pure eligibility policy without browser access.
if (typeof module !== 'undefined') module.exports = {eligible, cycle};
