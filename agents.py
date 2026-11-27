"""Linux telemetry, desktop application discovery and bounded responses."""
import hashlib
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
    'terminal': '터미널', 'files': '파일 탐색기', 'system-monitor': '시스템 모니터',
    'codium': 'VSCodium', 'pycharm': 'PyCharm', 'webstorm': 'WebStorm',
}
EXECUTABLES = {
    'gnome-terminal-server': 'terminal', 'gnome-terminal': 'terminal', 'kgx': 'terminal',
    'ptyxis': 'terminal', 'konsole': 'terminal', 'xterm': 'terminal', 'tilix': 'terminal',
    'alacritty': 'terminal', 'kitty': 'terminal', 'wezterm-gui': 'terminal', 'warp-terminal': 'terminal',
    'nautilus': 'files', 'dolphin': 'files', 'thunar': 'files', 'nemo': 'files', 'pcmanfm': 'files',
    'gnome-system-monitor': 'system-monitor',
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
    uid: int | None = None

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
    if proc.uid is not None and proc.uid != os.getuid():
        return None
    executable = Path(proc.executable).name.casefold()
    if executable == 'java':
        for arg in proc.command[1:]:
            key, sep, value = arg.partition('=')
            if sep and key == '-Didea.platform.prefix':
                return JAVA_PREFIXES.get(value.casefold())
    return EXECUTABLES.get(executable)


def get_process(pid, process=None, inventory=False):
    proc = process or psutil.Process(pid)
    with proc.oneshot():
        uid = proc.uids().real
        if (not inventory and any(value != os.getuid() for value in proc.uids())) or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        try:
            executable = proc.exe() or proc.name()
        except psutil.AccessDenied:
            executable = proc.name()
        try:
            command = tuple(proc.cmdline()) if uid == os.getuid() else ()
        except psutil.AccessDenied:
            command = ()
        return Process(pid, proc.ppid(), proc.create_time(), executable,
                       command, proc.memory_info().rss, cgroup_for(pid), uid)


def process_key(proc):
    return 'process:' + hashlib.sha256((str(proc.uid) + ':' + proc.executable).encode()).hexdigest()[:20]


def system_process(proc):
    if proc.pid == os.getpid():
        return True
    if proc.uid is not None and proc.uid != os.getuid():
        return True
    name = Path(proc.executable).name.casefold()
    critical = ('systemd', 'gnome-shell', 'dbus', 'pipewire', 'wireplumber', 'pulseaudio',
                'xdg-', 'gnome-session', 'gdm', 'xorg', 'xwayland', 'networkmanager',
                'gnome-keyring', 'ssh-agent', 'gpg-agent', 'dconf', 'at-spi', 'gvfs', 'ibus',
                'sd-pam', '(sd-pam)', 'evolution-', 'update-notifier', 'gsd-', 'chrome_crashpad', 'snapd', 'system76', 'polkit', 'kworker', 'kwin', 'plasmashell')
    if name.startswith(critical):
        return True
    if EXECUTABLES.get(name) in ('terminal', 'files', 'system-monitor'):
        return False
    scope = Path(proc.cgroup or '').name
    return scope.endswith('.service') and not scope.startswith('app-')


@dataclass
class App:
    key: str
    processes: list
    leaders: list
    display_name: str | None = None
    managed: bool = True
    protected_dependency: bool = False

    @property
    def rss(self):
        return sum(p.rss for p in self.processes)

    @property
    def name(self):
        return self.display_name or APP_NAMES.get(self.key, self.key)


def group_apps(processes):
    by_pid = {p.pid: p for p in processes}
    groups = {}
    ungrouped = []
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
        else:
            ungrouped.append(proc)
    for proc in ungrouped:
        key = process_key(proc)
        app = groups.setdefault(key, App(key, [], [], Path(proc.executable).name, True))
        app.processes.append(proc)
        app.managed = app.managed and not system_process(proc)
    for app in groups.values():
        if app.key.startswith('process:'):
            ids = {p.pid for p in app.processes}
            app.leaders = [p for p in app.processes if p.parent not in ids]
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
                p = get_process(proc.pid, proc, inventory=True)
                if p:
                    current.append(p)
            except (psutil.Error, OSError):
                pass
        return group_apps(current)


def priority(key, overrides):
    return overrides.get(key, 500)


def chrome_tab_management_enabled():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'probe'
    return (state / 'chrome-tab-management').is_file()


def has_protected_descendants(pid, settings):
    try:
        descendants = psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:
        return True
    for child in descendants:
        try:
            proc = get_process(child.pid, child)
            if proc and (system_process(proc) or priority(app_for(proc) or process_key(proc), settings) == 0):
                return True
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            return True
    return False


class ResponseAgent:
    @staticmethod
    def blocked_reason(app, overrides, unsuccessful_apps=()):
        if not app.managed:
            return 'system_process'
        if not 1 <= priority(app.key, overrides) <= 999:
            return 'protected'
        if app.protected_dependency:
            return 'protected_descendant'
        if app.key == 'chrome' and chrome_tab_management_enabled():
            return 'chrome_tab_mode'
        if len(app.leaders) != 1:
            return 'ambiguous_process'
        if app.key.startswith('process:') and app.name in ('java', 'java.bin'):
            return 'unknown_jvm'
        if app.rss < 32 * MIB:
            return 'insufficient_gain'
        if app.key in unsuccessful_apps:
            return 'previous_attempt_failed'
        return None

    @staticmethod
    def candidates(apps, overrides):
        by_pid = {p.pid: p for app in apps for p in app.processes}
        protected_ancestors = set()
        for app in apps:
            if priority(app.key, overrides) != 0:
                continue
            for proc in app.processes:
                parent = by_pid.get(proc.parent)
                seen = set()
                while parent and parent.pid not in seen:
                    seen.add(parent.pid)
                    protected_ancestors.add(parent.pid)
                    parent = by_pid.get(parent.parent)
        for app in apps:
            app.protected_dependency = any(p.pid in protected_ancestors for p in app.leaders)
        eligible = [app for app in apps if ResponseAgent.blocked_reason(app, overrides) is None]
        return sorted(eligible, key=lambda app: (-priority(app.key, overrides), -app.rss, app.key))

    @staticmethod
    def close(app, current_priorities, force=False):
        if (ResponseAgent.blocked_reason(app, current_priorities) is not None or len(app.leaders) != 1 or not 1 <= priority(app.key, current_priorities) <= 999
                or (app.key == 'chrome' and chrome_tab_management_enabled())):
            raise ValueError('현재 설정에서 종료할 수 없는 앱입니다')
        original = app.leaders[0]
        if has_protected_descendants(original.pid, current_priorities):
            raise ValueError('보호된 하위 앱이 있어 종료할 수 없습니다')
        # Sending through pidfd cannot signal a process that recycled this PID.
        fd = os.pidfd_open(original.pid)
        try:
            current = get_process(original.pid)
            if (not current or current.started != original.started
                    or current.executable != original.executable or current.helper
                    or (app_for(current) or process_key(current)) != app.key
                    or system_process(current)):
                raise ProcessLookupError('앱 프로세스가 바뀌었습니다')
            signal.pidfd_send_signal(fd, signal.SIGKILL if force else signal.SIGTERM)
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
            if not app.managed or priority(app.key, overrides) != 0:
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
