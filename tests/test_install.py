"""Exercise service-name migration without touching the running user systemd."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class InstallerTests(unittest.TestCase):
    def test_migration_keeps_config_and_alias_is_idempotent(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='memora-install-') as temp:
            base = Path(temp)
            repo = base / 'project with spaces'
            repo.mkdir()
            for file in ('install.sh', 'config.example.yml', 'window.py', 'history.py', 'priorities.py',
                         'chrome_tabs.py', 'response_labels.py', 'build_info.py', 'memory_pressure_manager.py'):
                shutil.copy(source / file, repo / file)
            shutil.copytree(source / 'extension', repo / 'extension')
            shutil.copytree(source / 'assets', repo / 'assets')
            (repo / '.venv/bin').mkdir(parents=True)
            (repo / '.venv/bin/pip').write_text('#!/bin/sh\nexit 0\n')
            (repo / '.venv/bin/pip').chmod(0o755)
            (repo / 'panel.sh').write_text('#!/bin/sh\nexit 0\n')
            (repo / 'panel.sh').chmod(0o755)
            (repo / 'config.yml').write_text('existing settings\n')
            stubs = base / 'bin'; stubs.mkdir()
            for name in ('python3', 'systemctl'):
                (stubs / name).write_text('#!/bin/sh\nexit 0\n')
                (stubs / name).chmod(0o755)
            config = base / 'config'
            units = config / 'systemd/user'; units.mkdir(parents=True)
            (units / 'probe.service').write_text('old service\n')
            env = {**os.environ, 'PATH': str(stubs) + ':' + os.environ['PATH'],
                   'XDG_CONFIG_HOME': str(config), 'XDG_DATA_HOME': str(base / 'data')}
            for _ in range(2):
                subprocess.run(['sh', './install.sh'], cwd=repo, env=env, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual((repo / 'config.yml').read_text(), 'existing settings\n')
                self.assertEqual((units / 'probe.service').readlink(), Path('memory-pressure-manager.service'))
                self.assertIn('/memory_pressure_manager.py', (units / 'memory-pressure-manager.service').read_text())
                self.assertEqual((units / 'probe.service.before-memory-pressure-manager').read_text(), 'old service\n')
            self.assertTrue((base / 'data/gnome-shell/extensions/probe@local/assets/intellij-idea.svg').is_file())
