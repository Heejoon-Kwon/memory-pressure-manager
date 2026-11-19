"""Linux telemetry, desktop application discovery and bounded responses."""
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

MIB = 1024**2
GIB = 1024**3
APP_NAMES = {
    'idea': 'IntelliJ IDEA', 'code': 'VS Code', 'datagrip': 'DataGrip',
    'chrome': 'Google Chrome', 'chromium': 'Chromium', 'firefox': 'Firefox',
    'slack': 'Slack', 'discord': 'Discord', 'edge': 'Microsoft Edge',
    'codium': 'VSCodium', 'pycharm': 'PyCharm', 'webstorm': 'WebStorm',
}
EXECUTABLES = {
    'idea': 'idea', 'idea.sh': 'idea', 'code': 'code', 'code-insiders': 'code',
    'codium': 'codium', 'datagrip': 'datagrip', 'datagrip.sh': 'datagrip',
    'chrome': 'chrome', 'google-chrome': 'chrome',
    'chromium': 'chromium', 'firefox': 'firefox', 'firefox-bin': 'firefox',
    'slack': 'slack', 'discord': 'discord', 'msedge': 'edge',
    'pycharm': 'pycharm', 'pycharm.sh': 'pycharm', 'webstorm': 'webstorm',
    'webstorm.sh': 'webstorm',
}
JAVA_PREFIXES = {
    'idea': 'idea', 'datagrip': 'datagrip', 'pycharm': 'pycharm', 'webstorm': 'webstorm',
}
IDE_DEFAULTS = {'idea', 'code', 'datagrip', 'codium'}
HELPER_FLAGS = ('--type=', '--utility-sub-type=', '-contentproc', '--ms-enable-electron-run-as-node')
USER_SCOPE = re.compile(r'app-(?!gnome-org\.gnome\.Shell)[\w.\\:@+-]+\.scope\Z', re.ASCII)


@dataclass(frozen=True)
class Sample:
    total: int
    available: int
    swap_in: int
    swap_out: int
    some: float | None
    full: float | None
    swap_used: int = 0

    @property
    def available_percent(self):
        return 100 * self.available / self.total


class WatchAgent:
    def __init__(self, psi=Path('/proc/pressure/memory')):
        self.psi = psi

    def sample(self):
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        pressure = {}
        try:
            for line in self.psi.read_text().splitlines():
                kind, *fields = line.split()
                values = dict(field.split('=', 1) for field in fields)
                pressure[kind] = float(values['avg10'])
        except (OSError, ValueError, KeyError):
            pressure = {}
        return Sample(memory.total, memory.available, swap.sin, swap.sout,
                      pressure.get('some'), pressure.get('full'), swap.used)


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    started: float
    executable: str
    command: tuple
    rss: int
    cgroup: str | None

    @property
    def helper(self):
        return any(arg.startswith(HELPER_FLAGS) for arg in self.command[1:])


def cgroup_for(pid):
    try:
        for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines():
            if line.startswith('0::/'):
                return line[3:]
    except OSError:
        pass
    return None


def app_for(proc):
    executable = Path(proc.executable).name.casefold()
    if executable == 'java':
        for arg in proc.command[1:]:
            key, sep, value = arg.partition('=')
            if sep and key == '-Didea.platform.prefix':
                return JAVA_PREFIXES.get(value.casefold())
    return EXECUTABLES.get(executable)


def get_process(pid, process=None):
    proc = process or psutil.Process(pid)
    with proc.oneshot():
        if any(uid != os.getuid() for uid in proc.uids()) or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return Process(pid, proc.ppid(), proc.create_time(), proc.exe(),
                       tuple(proc.cmdline()), proc.memory_info().rss, cgroup_for(pid))


@dataclass
class App:
    key: str
    processes: list
    leaders: list

    @property
    def rss(self):
        return sum(p.rss for p in self.processes)

    @property
    def name(self):
        return APP_NAMES[self.key]


def group_apps(processes):
    by_pid = {p.pid: p for p in processes}
    groups = {}
    for proc in processes:
        key = app_for(proc)
        parent = by_pid.get(proc.parent)
        seen = set()
        has_app_parent = False
        while parent and parent.pid not in seen and parent.started <= proc.started:
            seen.add(parent.pid)
            parent_key = app_for(parent)
            if parent_key:
                if not key or key == parent_key:
                    key = parent_key
                    has_app_parent = True
                break
            parent = by_pid.get(parent.parent)
        if key:
            app = groups.setdefault(key, App(key, [], []))
            app.processes.append(proc)
            if app_for(proc) == key and not has_app_parent and not proc.helper:
                app.leaders.append(proc)
    return sorted(groups.values(), key=lambda a: (-a.rss, a.name))


class AnalysisAgent:
    def __init__(self, critical_percent, confirm_samples):
        self.critical_percent = critical_percent
        self.confirm_samples = confirm_samples
        self.streak = 0

    def pressure(self, sample):
        low = sample.available_percent <= self.critical_percent
        stalled = (sample.some or 0) >= 15 and (sample.full or 0) >= 2
        self.streak = self.streak + 1 if low or (stalled and sample.available_percent < 18) else 0
        if self.streak >= self.confirm_samples:
            return 'critical'
        return 'warning' if low or stalled or sample.available_percent < 15 else 'normal'

    @staticmethod
    def applications():
        current = []
        for proc in psutil.process_iter():
            try:
                p = get_process(proc.pid, proc)
                if p:
                    current.append(p)
            except (psutil.Error, OSError):
                pass
        return group_apps(current)


def priority(key, overrides):
    return overrides.get(key, 0 if key in IDE_DEFAULTS else 4)


class ResponseAgent:
    @staticmethod
    def candidates(apps, overrides):
        eligible = [app for app in apps if 1 <= priority(app.key, overrides) <= 3
                    and len(app.leaders) == 1 and app.rss >= 128 * MIB]
        return sorted(eligible, key=lambda app: (priority(app.key, overrides), -app.rss, app.key))

    @staticmethod
    def close(app, current_priorities):
        if len(app.leaders) != 1 or priority(app.key, current_priorities) not in (1, 2, 3):
            raise ValueError('현재 설정에서 종료할 수 없는 앱입니다')
        original = app.leaders[0]
        # Sending through pidfd cannot signal a process that recycled this PID.
        fd = os.pidfd_open(original.pid)
        try:
            current = get_process(original.pid)
            if (not current or current.started != original.started
                    or current.executable != original.executable or current.helper
                    or app_for(current) != app.key):
                raise ProcessLookupError('앱 프로세스가 바뀌었습니다')
            signal.pidfd_send_signal(fd, signal.SIGTERM)
        finally:
            os.close(fd)


class OOMPreference:
    """Best-effort systemd-oomd preference for a user's dedicated app scope."""
    def __init__(self):
        self.applied = {}
        self.failed = set()

    @staticmethod
    def command(*args):
        result = subprocess.run(('systemctl', '--user', *args), capture_output=True,
                                text=True, timeout=2, check=True)
        return result.stdout.strip()

    def sync(self, apps, overrides):
        wanted = {}
        for app in apps:
            if priority(app.key, overrides) != 0:
                continue
            for p in app.leaders:
                scope = Path(p.cgroup or '').name
                if p.cgroup and f'/user-{os.getuid()}.slice/' in p.cgroup and USER_SCOPE.fullmatch(scope):
                    wanted[scope] = p.cgroup
        self.failed.intersection_update(wanted)
        for scope, (group, previous) in tuple(self.applied.items()):
            if wanted.get(scope) != group:
                self.restore(scope, group, previous)
        for scope, group in wanted.items():
            if scope in self.applied or scope in self.failed:
                continue
            try:
                actual, preference = self.read(scope)
                if actual != group:
                    continue
                # Verify that this is a leaf scope and every member belongs to the same app.
                folder = Path('/sys/fs/cgroup') / group.lstrip('/')
                members = {int(pid) for pid in (folder / 'cgroup.procs').read_text().split()}
                app_members = [set(p.pid for p in app.processes)
                               for app in apps if priority(app.key, overrides) == 0]
                if (not members or not any(members <= ids for ids in app_members)
                        or any(p.is_dir() for p in folder.iterdir())):
                    continue
                if preference == 'none':
                    self.command('set-property', '--runtime', scope, 'ManagedOOMPreference=avoid')
                    self.applied[scope] = (group, preference)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.failed.add(scope)

    def read(self, scope):
        output = self.command('show', scope, '-p', 'ControlGroup', '-p', 'ManagedOOMPreference')
        data = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        return data.get('ControlGroup'), data.get('ManagedOOMPreference')

    def restore(self, scope, group, previous):
        self.applied.pop(scope, None)
        try:
            if self.read(scope) == (group, 'avoid'):
                self.command('set-property', '--runtime', scope, 'ManagedOOMPreference=' + previous)
        except (OSError, subprocess.SubprocessError):
            pass

    def stop(self):
        for scope, (group, previous) in tuple(self.applied.items()):
            self.restore(scope, group, previous)
