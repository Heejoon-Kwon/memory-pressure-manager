"""Bounded proactive reclaim on writable, dedicated, unprotected user app scopes."""
import errno
import json
import os
import sqlite3
from pathlib import Path
from agents import MIB, USER_SCOPE, priority, has_protected_descendants, get_process

RULE = 'reclaim_cgroup'


def target_for(app, settings, root=Path('/sys/fs/cgroup')):
    if not app.managed or priority(app.key, settings) == 0 or app.protected_dependency or len(app.leaders) != 1:
        return None
    proc = app.leaders[0]
    group = proc.cgroup
    if not group or '..' in Path(group).parts or f'/user-{os.getuid()}.slice/' not in group or not USER_SCOPE.fullmatch(Path(group).name):
        return None
    folder = (root / group.lstrip('/')).resolve()
    if not folder.is_relative_to(root.resolve()):
        return None
    try:
        members = {int(x) for x in (folder/'cgroup.procs').read_text().split()}
        if not members or not members.issubset({p.pid for p in app.processes}) or any(p.is_dir() for p in folder.iterdir()):
            return None
        if not os.access(folder/'memory.reclaim', os.W_OK):
            return None
    except (OSError, ValueError):
        return None
    return folder


def reclaim(app, settings, amount, swappiness):
    folder = target_for(app, settings)
    proc = get_process(app.leaders[0].pid)
    if (folder is None or not proc or proc.started != app.leaders[0].started
            or has_protected_descendants(proc.pid, settings)):
        raise ValueError('reclaim target changed or protected')
    try:
        with (folder/'memory.reclaim').open('w') as output:
            output.write(f'{amount} swappiness={swappiness}')
    except OSError as exc:
        if exc.errno != errno.EAGAIN:
            raise


def compressed_headroom():
    free = 0
    for device in Path('/sys/block').glob('zram*'):
        try:
            size = int((device/'disksize').read_text())
            original = int((device/'mm_stat').read_text().split()[0])
            free += max(0, size-original)
        except (OSError, ValueError, IndexError):
            continue
    return free


class ReclaimResponse:
    def __init__(self, owner, config):
        self.owner = owner
        cfg = config.get('memory_optimization', {})
        self.enabled = cfg.get('proactive_reclaim', True)
        self.batch = max(32, min(256, int(cfg.get('reclaim_batch_mib',64)))) * MIB
        self.pending = None
        self.trial = None
        self.next_at = 0
        self.summary = ''
        self.capability = '회수 권한 확인 대기'
        self.api_failed = False

    def tick(self, sample, level, now, apps, settings):
        o = self.owner
        emergency = sample.available_percent <= 3 or (sample.full or 0) >= 10
        if emergency:
            self.pending = None
            if self.trial:
                o.event('optimization_cancelled', {'app':RULE,'reason':'emergency_overlap'})
                self.trial = None
            self.next_at = now + 90
            return False
        if self.trial:
            if now < self.trial['verify_at']:
                self.summary = '앱 종료 없이 페이지 회수 효과 관찰 중'
                return True
            trial, self.trial = self.trial, None
            gain = sample.available - trial['before'].available
            success = gain >= 32*MIB and (sample.full or 0) <= (trial['before'].full or 0) + .5
            if success:
                o.skills.save(trial['scenario'], RULE)
            else:
                o.skills.discard(trial['scenario'])
            o.event('skill_saved' if success else 'recovery_failed', {'app':RULE,'scenario':trial['scenario'],
                    'source':trial['source'],'before_mib':round(trial['before'].available/MIB),'after_mib':round(sample.available/MIB)})
            self.summary = '페이지 회수 효과 확인 · 스킬 저장' if success else '페이지 회수 효과 미확인 · 다음 대응 단계'
            return True
        if self.pending:
            kind, future, app, scenario, source = self.pending
            if not future.done():
                self.summary = '페이지 회수 판단 중' if kind == 'ai' else '앱 종료 없이 페이지 회수 중'
                return True
            self.pending = None
            try:
                result = future.result()
                if kind == 'ai' and result == 'none':
                    self.next_at = now + 90
                    return False
                if kind == 'ai' and level == 'normal':
                    self.next_at = now + 90
                    return False
                if kind == 'ai':
                    return self.start(app, sample, now, scenario, 'ai')
            except Exception as exc:
                o.event('optimization_error', {'app':RULE,'error':type(exc).__name__})
                if kind == 'ai': self.api_failed = True
                self.next_at = now + 90
                return False
            self.trial = {'before':self.before, 'scenario':scenario, 'source':source, 'verify_at':now+10}
            return True
        if not self.enabled or level == 'normal' or now < self.next_at or (sample.full or 0) >= 2 or (sample.some or 0) >= 15:
            return False
        eligible = [a for a in apps if a.rss >= 64*MIB and target_for(a, settings)]
        self.capability = f'종료 없는 회수 가능 {len(eligible)}개 앱' if eligible else '종료 없는 회수: 위임된 cgroup 권한 없음 또는 대상 없음'
        if not eligible:
            self.next_at = now + 30
            return False
        app = sorted(eligible,key=lambda a:(-priority(a.key,settings),-a.rss))[0]
        # Anonymous offloading is enabled only with compressed swap and ample headroom.
        import psutil
        self.swappiness = 100 if compressed_headroom() >= 512*MIB and psutil.swap_memory().free >= 512*MIB else 0
        scenario = json.dumps(['reclaim-v1', self.swappiness, []])
        with sqlite3.connect(o.database) as db:
            learned = db.execute('SELECT app FROM skills WHERE scenario=?',(scenario,)).fetchone()
        if learned and learned[0] == RULE:
            return self.start(app, sample, now, scenario, 'skill')
        if o.advisor.enabled and not self.api_failed:
            self.pending = ('ai', o.executor.submit(o.ask_advisor, scenario,
                {'available_mib':round(sample.available/MIB),'psi_full_avg10':sample.full,'batch_mib':self.batch//MIB,
                 'swappiness':self.swappiness,'non_terminating':True}, [{'id':RULE,'action':'bounded_page_reclaim'}]),app,scenario,'ai')
            return True
        return self.start(app,sample,now,scenario,'local')

    def start(self, app, sample, now, scenario, source):
        from probe import overrides
        self.next_at = now+90
        self.before = sample
        self.owner.event('optimization_requested', {'app':RULE,'source':source,'batch_mib':self.batch//MIB})
        # Run potentially blocking kernel reclaim in the worker. Never on the monitoring thread.
        self.pending = ('reclaim',self.owner.executor.submit(reclaim,app,overrides(),self.batch,self.swappiness),app,scenario,source)
        self.summary = '앱 종료 없이 페이지 회수 요청'
        return True
