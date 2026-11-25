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
// A native port keeps the service worker alive for live local-window updates.
// Only titles and opaque IDs cross the bridge. URLs stay in Chrome session storage.
let bridge = null;
let bridgeTimer = null;
let syncing = false;
async function profileId() {
  const data = await chrome.storage.local.get('probeProfileId');
  if (data.probeProfileId) return data.probeProfileId;
  const id = crypto.randomUUID();
  await chrome.storage.local.set({probeProfileId: id});
  return id;
}
async function syncWindow() {
  if (!bridge || syncing) return;
  syncing = true;
  try {
    const [profile, tabs, session] = await Promise.all([
      profileId(), chrome.tabs.query({}), chrome.storage.session.get(['tabPriorities', 'tabIdentities'])
    ]);
    const priorities = session.tabPriorities || {};
    const old = session.tabIdentities || {};
    const identities = {};
    const entries = [];
    for (const tab of tabs.filter(t => !t.incognito).slice(0, 500)) {
      const previous = old[tab.id];
      const identity = previous && previous.url === tab.url ? previous : {url: tab.url || '', token: crypto.randomUUID()};
      identities[tab.id] = identity;
      const setting = priorities[tab.id];
      const value = setting && setting.url === tab.url && Number.isInteger(setting.priority) &&
        setting.priority >= 1 && setting.priority <= 999 ? setting.priority : 0;
      entries.push({id: tab.id, token: identity.token, title: (tab.title || '제목 없는 탭').slice(0, 160),
        priority: value, active: !!tab.active, pinned: !!tab.pinned, audible: !!tab.audible,
        discarded: !!tab.discarded, supported: typeof tab.url === 'string' && /^https?:\/\//i.test(tab.url)});
    }
    await chrome.storage.session.set({tabIdentities: identities});
    if (bridge) bridge.postMessage({type: 'sync', profile, tabs: entries});
  } finally { syncing = false; }
}
async function applyCommands(commands) {
  if (!Array.isArray(commands)) return;
  for (const cmd of commands) {
    if (!Number.isInteger(cmd.id) || !Number.isInteger(cmd.priority) || cmd.priority < 0 || cmd.priority > 999) continue;
    try {
      const tab = await chrome.tabs.get(cmd.id);
      const session = await chrome.storage.session.get(['tabPriorities', 'tabIdentities']);
      const identity = (session.tabIdentities || {})[cmd.id];
      if (!identity || identity.token !== cmd.token || identity.url !== tab.url || tab.incognito ||
          typeof tab.url !== 'string' || !/^https?:\/\//i.test(tab.url)) continue;
      const all = session.tabPriorities || {};
      if (cmd.priority === 0) delete all[cmd.id];
      else all[cmd.id] = {url: tab.url, priority: cmd.priority};
      await chrome.storage.session.set({tabPriorities: all});
    } catch (_) { /* tab was closed or changed */ }
  }
  await syncWindow();
}
function connectBridge() {
  if (bridge) return;
  try {
    const port = chrome.runtime.connectNative(HOST);
    bridge = port;
    port.onMessage.addListener(message => {
      if (message.type === 'sync' && message.commands?.length) applyCommands(message.commands).catch(() => {});
    });
    port.onDisconnect.addListener(() => {
      // Consume lastError to avoid noisy console warnings when the host is not installed.
      void chrome.runtime.lastError;
      if (bridge === port) bridge = null;
      if (bridgeTimer) clearInterval(bridgeTimer);
      bridgeTimer = null;
    });
    bridgeTimer = setInterval(() => syncWindow().catch(() => {}), 3000);
    syncWindow().catch(() => {});
  } catch (_) { bridge = null; }
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM && !bridge) connectBridge(); });
if (typeof module === 'undefined') connectBridge();
// Testable pure eligibility policy without browser access.
if (typeof module !== 'undefined') module.exports = {eligible, cycle, applyCommands};
