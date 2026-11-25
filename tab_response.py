"""AI -> tab discard -> measured verification -> reusable rule. No tab content sent to AI."""
import json
import sqlite3
from chrome_tabs import candidates, request_discard, snapshots
from agents import MIB

RULE = 'chrome_tab_highest_priority'


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
            gain = sample.available - trial['before'].available
            recovered = observed and gain >= max(32 * MIB, int(sample.total * .002)) and (
                sample.full is None or sample.full < 2 or sample.full <= (trial['before'].full or 0) / 2)
            details = {'scenario': trial['scenario'], 'app': RULE, 'source': trial['source'],
                       'before_mib': round(trial['before'].available / MIB), 'after_mib': round(sample.available / MIB)}
            if recovered:
                o.skills.save(trial['scenario'], RULE)
                o.event('skill_saved', details)
                self.summary = '탭 회수 후 메모리 회복 확인 · 스킬 저장'
            else:
                o.skills.discard(trial['scenario'])
                o.event('recovery_failed', details)
                self.summary = '탭 회수 효과 미확인 · 해당 스킬 해제'
            self.trial = None
            return True
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
            # Live candidates and policy are checked below, never old tab IDs.
            return self.act(sample, level, now, source)
        if level != 'critical' or now < self.next_action or self.declined:
            return False
        eligible = candidates(self.runtime)
        if not eligible:
            return False
        scenario = self.scenario(sample, eligible)
        with sqlite3.connect(o.database) as db:
            learned = db.execute('SELECT app FROM skills WHERE scenario=?', (scenario,)).fetchone()
        if learned and learned[0] == RULE:
            return self.act(sample, level, now, 'skill')
        if o.advisor.enabled and not self.ai_failed:
            telemetry = {'available_mib': round(sample.available / MIB), 'total_mib': round(sample.total / MIB),
                         'psi_some_avg10': sample.some, 'psi_full_avg10': sample.full,
                         'eligible_tab_count': len(eligible), 'highest_discard_priority': eligible[0]['priority'],
                         'action': 'discard one inactive unprotected browser tab; reload when revisited',
                         'tab_rss_available': False}
            self.future = o.executor.submit(o.ask_advisor, scenario, telemetry, [{'id': RULE, 'action': 'discard_tab'}])
            self.summary = 'AI에 Chrome 탭 회수 판단 요청'
            return True
        return self.act(sample, level, now, 'local')

    @staticmethod
    def scenario(sample, eligible):
        return json.dumps(['chrome-tab-v1', 'stall' if (sample.full or 0) >= 2 else 'low-available',
                           eligible[0]['priority']])

    def act(self, sample, level, now, source):
        if level != 'critical':
            return False
        eligible = candidates(self.runtime)
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
        self.next_action = now + self.owner.cooldown
        self.trial = {'tab': tab, 'before': sample, 'scenario': scenario, 'source': source,
                      'verify_at': now + self.owner.verify_seconds}
        self.owner.event('action_requested', {'scenario': scenario, 'app': RULE, 'source': source})
        self.summary = 'Chrome 탭 회수 요청 · 회복 검증 대기'
        return True
