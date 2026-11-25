"""Private local exchange between Chrome native bridge and the GTK window."""
import fcntl
import json
import os
import re
import tempfile
import time
from pathlib import Path

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
MAX_TABS = 500


def valid_profile(value):
    return isinstance(value, str) and bool(UUID.fullmatch(value))


def folder(base):
    path = Path(base) / 'chrome-tabs'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if path.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('unsafe chrome-tabs directory')
    return path


def write_atomic(path, value):
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def clean_tabs(tabs):
    result = []
    if not isinstance(tabs, list) or len(tabs) > MAX_TABS:
        raise ValueError('invalid tabs')
    for item in tabs:
        if not isinstance(item, dict):
            raise ValueError('invalid tab')
        ident, token, title, prio = (item.get(k) for k in ('id', 'token', 'title', 'priority'))
        if (type(ident) is not int or ident < 0 or not valid_profile(token)
                or not isinstance(title, str) or type(prio) is not int or not 0 <= prio <= 999):
            raise ValueError('invalid tab metadata')
        result.append({'id': ident, 'token': token, 'title': title[:160], 'priority': prio,
                       'active': item.get('active') is True, 'pinned': item.get('pinned') is True,
                       'audible': item.get('audible') is True, 'discarded': item.get('discarded') is True,
                       'supported': item.get('supported') is True})
    return result


def sync(base, profile, tabs):
    if not valid_profile(profile):
        raise ValueError('invalid profile')
    items = clean_tabs(tabs)
    path = folder(base)
    write_atomic(path / (profile + '.json'), {'timestamp': time.time(), 'profile': profile, 'tabs': items})
    pending = path / (profile + '.queue')
    with (path / (profile + '.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            commands = json.loads(pending.read_text())
        except (OSError, ValueError):
            commands = []
        pending.unlink(missing_ok=True)
    return commands if isinstance(commands, list) else []


def snapshots(base, max_age=20):
    path = Path(base) / 'chrome-tabs'
    result = []
    if not path.is_dir():
        return result
    for file in path.glob('*.json'):
        try:
            data = json.loads(file.read_text())
            age = time.time() - float(data['timestamp'])
            if 0 <= age <= max_age and valid_profile(data['profile']):
                data['tabs'] = clean_tabs(data['tabs'])
                result.append(data)
        except (ValueError, OSError, TypeError, KeyError):
            continue
    return sorted(result, key=lambda x: x['profile'])


def enqueue(base, profile, tab_id, token, value):
    if (not valid_profile(profile) or not valid_profile(token) or type(tab_id) is not int
            or type(value) is not int or not 0 <= value <= 999):
        raise ValueError('invalid change')
    items = next((x['tabs'] for x in snapshots(base) if x['profile'] == profile), [])
    tab = next((t for t in items if t['id'] == tab_id and t['token'] == token), None)
    if not tab or not tab['supported']:
        raise ValueError('tab no longer available')
    path = folder(base)
    pending = path / (profile + '.queue')
    with (path / (profile + '.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            commands = json.loads(pending.read_text())
            if not isinstance(commands, list):
                commands = []
        except (OSError, ValueError):
            commands = []
        commands = [x for x in commands if isinstance(x, dict) and x.get('id') != tab_id]
        commands.append({'id': tab_id, 'token': token, 'priority': value})
        write_atomic(pending, commands[-MAX_TABS:])


def candidates(base):
    items = []
    for profile in snapshots(base):
        for tab in profile['tabs']:
            if tab['priority'] > 0 and tab['supported'] and not any(tab[x] for x in ('active', 'pinned', 'audible', 'discarded')):
                items.append({**tab, 'profile': profile['profile']})
    return sorted(items, key=lambda t: (-t['priority'], t['profile'], t['id']))


def request_discard(base, tab):
    # Revalidate the current snapshot before queuing an action. Chrome rechecks again.
    current = next((t for t in candidates(base) if t['profile'] == tab['profile']
                    and t['id'] == tab['id'] and t['token'] == tab['token']), None)
    if current is None:
        raise ValueError('tab is no longer eligible')
    path = folder(base)
    pending = path / (tab['profile'] + '.queue')
    with (path / (tab['profile'] + '.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            commands = json.loads(pending.read_text())
        except (OSError, ValueError):
            commands = []
        if commands:
            raise ValueError('priority changes are pending')
        write_atomic(pending, [{'id': tab['id'], 'token': tab['token'], 'action': 'discard', 'expires': time.time() + 12}])
