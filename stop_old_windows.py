"""Retire only owned management windows from known installation paths."""
import json
import os
from pathlib import Path
import psutil


def main():
    root = Path(__file__).resolve().parent
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    allowed = {root / 'window.py', data / 'gnome-shell/extensions/probe@local/window.py'}
    for proc in psutil.process_iter(['uids', 'cmdline']):
        try:
            if proc.info['uids'].real != os.getuid():
                continue
            command = proc.info['cmdline'] or []
            if len(command) < 2:
                continue
            script = Path(command[1])
            if not script.is_absolute():
                script = Path(proc.cwd()) / script
            script = script.resolve()
            if script in allowed:
                proc.terminate()
            elif script.name in ('probe.py', 'memora.py', 'memory_pressure_manager.py'):
                try:
                    metadata = json.loads((script.parent / 'extension/metadata.json').read_text())
                except (OSError, ValueError):
                    continue
                if metadata.get('uuid') == 'probe@local' and (script.parent / 'agents.py').is_file():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except psutil.TimeoutExpired:
                        raise SystemExit('이전 감시 프로세스가 아직 종료 중입니다. 잠시 후 설치를 다시 실행하세요.')
        except (psutil.Error, OSError):
            continue


if __name__ == '__main__':
    main()
