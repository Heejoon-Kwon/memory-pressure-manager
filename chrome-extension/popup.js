const list = document.getElementById('tabs');
const status = document.getElementById('status');
async function render() {
  try {
    const result = await chrome.runtime.sendMessage({type: 'test-connection'});
    status.textContent = result?.error ? 'Probe 연결 안 됨 · 설치 안내를 확인하세요' :
      result?.running ? 'Probe 연결됨 · ' + (result.critical ? '메모리 압박 감지' : '감시 중') : 'Probe 연결됨 · 서비스가 실행 중이 아닙니다';
  } catch (_) { status.textContent = 'Probe 연결 안 됨 · 설치 안내를 확인하세요'; }
  const tabs = await chrome.tabs.query({});
  const data = await chrome.storage.session.get('tabPriorities');
  const priorities = data.tabPriorities || {};
  list.replaceChildren();
  document.getElementById('empty').hidden = tabs.length > 0;
  for (const tab of tabs) {
    const row = document.createElement('div'); row.className = 'tab';
    const info = document.createElement('div');
    const title = document.createElement('div'); title.className = 'name'; title.textContent = tab.title || '제목 없는 탭';
    const url = document.createElement('div'); url.className = 'url';
    try { url.textContent = new URL(tab.url).hostname || '브라우저 내부 페이지'; } catch { url.textContent = '브라우저 내부 페이지'; }
    info.append(title, url);
    const label = document.createElement('label'); label.className = 'protect';
    const check = document.createElement('input'); check.type = 'checkbox';
    const value = priorities[tab.id];
    check.checked = !(value && value.url === tab.url && Number.isInteger(value.priority) && value.priority >= 1 && value.priority <= 999);
    label.append(check, document.createTextNode(' 보호'));
    const number = document.createElement('input'); number.type = 'number'; number.min = '1'; number.max = '999'; number.value = check.checked ? '500' : value.priority; number.disabled = check.checked;
    const supported = !!tab.url && /^https?:\/\//i.test(tab.url) && !tab.incognito;
    check.disabled = !supported;
    if (!supported) { check.checked = true; number.disabled = true; label.title = '일반 웹 탭만 설정할 수 있습니다.'; }
    async function save() {
      number.disabled = check.checked;
      const current = await chrome.storage.session.get('tabPriorities');
      const all = current.tabPriorities || {};
      const n = Number(number.value);
      if (check.checked || !Number.isInteger(n) || n < 1 || n > 999) {
        delete all[tab.id];
        if (!check.checked) { check.checked = true; number.disabled = true; }
      } else all[tab.id] = {url: tab.url, priority: n};
      await chrome.storage.session.set({tabPriorities: all});
    }
    check.addEventListener('change', save); number.addEventListener('change', save);
    row.append(info, label, number); list.append(row);
  }
}
document.getElementById('refresh').addEventListener('click', render);
render();
