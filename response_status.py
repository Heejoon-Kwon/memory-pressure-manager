"""Explain policy decisions without changing the set of allowed actions."""
from agents import ResponseAgent
from chrome_tabs import snapshots


def tab_counts(base):
    try:
        profiles = snapshots(base)
    except (OSError, ValueError):
        return {'profiles': 0, 'allowed': 0, 'eligible': 0, 'waiting': 0}
    allowed = eligible = 0
    for profile in profiles:
        for tab in profile['tabs']:
            if tab['priority'] and tab['supported']:
                allowed += 1
                if not any(tab[flag] for flag in ('active', 'pinned', 'audible', 'discarded')):
                    eligible += 1
    return {'profiles': len(profiles), 'allowed': allowed, 'eligible': eligible, 'waiting': allowed - eligible}


def explain(apps, settings, unsuccessful_apps, base):
    blocked = []
    for app in apps:
        reason = ResponseAgent.blocked_reason(app, settings, unsuccessful_apps)
        if reason and reason != 'protected':
            blocked.append({'app': app.key, 'reason': reason})
    tabs = tab_counts(base)
    if tabs['eligible']:
        summary = f"앱 종료 후보 없음 · Chrome 탭 {tabs['eligible']}개 회수 가능"
    elif tabs['allowed']:
        summary = f"앱 종료 후보 없음 · Chrome 탭 {tabs['allowed']}개 일시 제외"
    elif blocked:
        summary = '종료 허용 앱은 있으나 현재 실행 가능한 앱 없음'
    else:
        summary = '종료 허용 앱과 회수 가능한 Chrome 탭 없음'
    return {'summary': summary, 'blocked': blocked, 'tabs': tabs}
