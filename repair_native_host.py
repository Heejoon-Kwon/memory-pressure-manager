"""Keep registered extension origins but move the native bridge to the installed checkout."""
import json
import os
import tempfile
from pathlib import Path


def main():
    path = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'google-chrome/NativeMessagingHosts/local.probe.tabs.json'
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        print('Chrome 연결 설정을 읽지 못했습니다. 호스트 등록 명령으로 다시 연결하세요.')
        return
    if data.get('name') != 'local.probe.tabs' or data.get('type') != 'stdio':
        return
    data['path'] = str(Path(__file__).resolve().with_name('chrome_native_host.py'))
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(data, output, indent=2)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
