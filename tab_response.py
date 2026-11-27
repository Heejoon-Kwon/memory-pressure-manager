"""AI -> tab discard -> measured verification -> reusable rule. No tab content sent to AI."""
import json
import sqlite3
import time
from chrome_tabs import candidates, request_discard, snapshots, action_done
from agents import MIB

RULE = 'chrome_tab_highest_priority'
WEB_RULE = 'chrome_web_app_close'

def rule_for(tab):
    return WEB_RULE if tab.get('standalone') else RULE


class TabResponse:
    def __init__(self, owner, runtime):
        self.owner, self.runtime = owner, runtime
        self.future = None
        self.trial = None
        self.next_action = 0
        self.declined = False
        self.ai_failed = False
        self.summary = ''

    def tick(self, sample, level, now):
        o = self.owner
        if level == 'normal':
            self.declined = False
        if self.trial:
            trial = self.trial
            if now < trial['verify_at']:
                self.summary = 'Chrome 탭 회수 결과 확인 중'
                return True
            tab = trial['tab']
            observed = any(t['id'] == tab['id'] and t['token'] == tab['token'] and t['discarded']
                           for p in snapshots(self.runtime) if p['profile'] == tab['profile'] for t in p['tabs'])
            observed = observed or action_done(self.runtime, tab, trial['requested_at'])
            gain = sample.available - trial['before'].available
            recovered = observed and gain >= max(32 * MIB, int(sample.total * .002)) and (
                sample.full is None or sample.full < 2 or sample.full <= (trial['before'].full or 0) / 2)
            details = {'scenario': trial['scenario'], 'app': trial['rule'], 'source': trial['source'],
                       'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)}
            if recovered:
                o.skills.save(trial['scenario'], trial['rule'])
                o.event('action_effective' if trial['rule'] == WEB_RULE else 'skill_saved', details)
                self.summary = '탭 회수 후 메모리 회복 확인 · 스킬 저장'
            else:
                o.skills.discard(trial['scenario'])
                o.event('recovery_failed', details)
                self.summary = '탭 회수 효과 미확인 · 해당 스킬 해제'
            self.trial = None
            return True
        emergency = sample.available_percent <= 3 or ((sample.full or 0) >= 10 and sample.available_percent < 8)
        if emergency and self.future and not self.future.done() and not self.trial:
            self.future = None
            return self.act(sample, level, now, 'emergency')
        if self.future:
            if not self.future.done():
                self.summary = 'AI가 Chrome 탭 메모리 회수 판단 중'
                return True
            future, self.future = self.future, None
            source = 'ai'
            try:
                choice = future.result()
            except Exception as exc:
                o.event('ai_error', {'app': RULE, 'error': type(exc).__name__})
                choice = RULE
                source = 'local'
                self.ai_failed = True  # no automatic API retry after an error
                self.summary = 'AI 오류 · 로컬 탭 우선순위 적용'
            if choice == 'none':
                self.declined = True
                self.summary = 'AI가 이번 압박 상황의 탭 회수 보류'
                return True
            current = candidates(self.runtime)
            if not current or rule_for(current[0]) != self.requested_rule:
                return False
            # Live candidates and policy are checked below, never old tab IDs.
            return self.act(sample, level, now, source)
        if level not in ('warning','critical') or now < self.next_action or self.declined:
            return False
        eligible = [t for t in candidates(self.runtime) if not t.get('standalone') or (level == 'critical' and sample.available_percent <= min(getattr(self.owner,'threshold',8),8))]
        if not eligible:
            return False
        scenario = self.scenario(sample, eligible)
        with sqlite3.connect(o.database) as db:
            learned = db.execute('SELECT app FROM skills WHERE scenario=?', (scenario,)).fetchone()
        if learned and learned[0] == rule_for(eligible[0]):
            return self.act(sample, level, now, 'skill')
        if emergency:
            return self.act(sample, level, now, 'emergency')
        return self.act(sample, level, now, 'local')

    @staticmethod
    def scenario(sample, eligible):
        return json.dumps(['chrome-web-app-v1' if eligible[0].get('standalone') else 'chrome-tab-v1', 'stall' if (sample.full or 0) >= 2 else 'low-available',
                           eligible[0]['priority']])

    def act(self, sample, level, now, source):
        if level not in ('warning','critical'):
            return False
        eligible = [t for t in candidates(self.runtime) if not t.get('standalone') or (level == 'critical' and sample.available_percent <= min(getattr(self.owner,'threshold',8),8))]
        if not eligible:
            return False
        tab = eligible[0]
        scenario = self.scenario(sample, eligible)
        try:
            request_discard(self.runtime, tab)
        except (ValueError, OSError):
            self.next_action = now + self.owner.interval
            self.summary = 'Chrome 탭 상태 변경 · 재확인 대기'
            return True
        emergency = sample.available_percent <= 3 or ((sample.full or 0) >= 10 and sample.available_percent < 8)
        self.next_action = now + 3
        self.trial = {'tab': tab, 'before': sample, 'scenario': scenario, 'source': source,
                      'verify_at': now + min(self.owner.verify_seconds, 6), 'requested_at': time.time(), 'rule': rule_for(tab)}
        self.owner.event('action_requested', {'scenario': scenario, 'app': rule_for(tab), 'source': source})
        self.summary = 'Chrome 탭 회수 요청 · 회복 검증 대기'
        return True
