const assert = require('node:assert/strict');
global.chrome = {runtime:{onInstalled:{addListener(){}},onStartup:{addListener(){}},onMessage:{addListener(){}}},alarms:{onAlarm:{addListener(){}}},tabs:{onRemoved:{addListener(){}}}};
const {eligible, cycle, applyCommands} = require('../chrome-extension/background.js');
const tab = {id:1,url:'https://example.com/',active:false,pinned:false,audible:false,discarded:false,incognito:false};
const setting = {url:tab.url,priority:999};
assert.equal(eligible({tab,setting}),true);
for (const flag of ['active','pinned','audible','discarded','incognito']) assert.equal(eligible({tab:{...tab,[flag]:true},setting}),false,flag);
assert.equal(eligible({tab,setting:undefined}),false);
assert.equal(eligible({tab,setting:{...setting,url:'https://other.example/'}}),false);
assert.equal(eligible({tab,setting:{...setting,priority:0}}),false);
assert.equal(eligible({tab,setting:{...setting,priority:1000}}),false);
assert.equal(eligible({tab:{...tab,url:'chrome://settings/'},setting:{url:'chrome://settings/',priority:1}}),false);

(async () => {
  const tab2 = {...tab, id: 2, url: 'https://other.example/'};
  let discarded = [];
  let granted = false;
  let afterClaim = null;
  chrome.tabs.query = async () => [tab, tab2];
  chrome.tabs.discard = async id => discarded.push(id);
  chrome.storage = {session: {get: async () => ({tabPriorities: {
    1: {url: tab.url, priority: 100}, 2: {url: tab2.url, priority: 900}}})}};
  chrome.runtime.sendNativeMessage = async (_host, message) => {
    if (message.type === 'claim') { if (afterClaim) afterClaim(); return {granted}; }
    return {ok: true};
  };
  await cycle();
  assert.deepEqual(discarded, []);
  granted = true;
  await cycle();
  assert.deepEqual(discarded, [2]);
  discarded = [];
  afterClaim = () => { tab2.active = true; };
  await cycle();
  assert.deepEqual(discarded, [1]);
  const identities = {1: {url: tab.url, token: 'session-a'}};
  let state = {tabIdentities: identities, tabPriorities: {}};
  chrome.tabs.get = async () => tab;
  chrome.storage.session.get = async () => state;
  chrome.storage.session.set = async obj => { state = {...state, ...obj}; };
  await applyCommands([{id: 1, token: 'stale-session', priority: 900}]);
  assert.equal(state.tabPriorities[1], undefined);
  await applyCommands([{id: 1, token: 'session-a', priority: 900}]);
  assert.equal(state.tabPriorities[1].priority, 900);
  await applyCommands([{id: 1, token: 'session-a', priority: 0}]);
  assert.equal(state.tabPriorities[1], undefined);
})().catch(error => { console.error(error); process.exitCode = 1; });
