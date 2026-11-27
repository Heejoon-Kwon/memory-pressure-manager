#!/usr/bin/env python3
"""Install a root-owned helper for the invoking desktop user. No API key is read."""
import os
from pathlib import Path
import shutil
import subprocess


def main():
    uid=int(os.environ.get('SUDO_UID','0'))
    if os.geteuid()!=0 or uid<=0:
        raise SystemExit('실행: sudo .venv/bin/python install_kernel_guard.py')
    root=Path('/usr/local/lib/memory-pressure-manager')
    root.mkdir(mode=0o755,parents=True,exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != 0 or root.stat().st_mode & 0o022:
        raise SystemExit('helper 폴더는 root 소유이며 다른 사용자가 수정할 수 없어야 합니다')
    destination=root/'kernel_guard.py'
    if destination.is_symlink(): raise SystemExit('helper 경로가 심볼릭 링크입니다')
    shutil.copyfile(Path(__file__).with_name('kernel_guard.py'),destination)
    os.chown(destination,0,0); os.chmod(destination,0o644)
    unit=Path('/etc/systemd/system')/f'memory-pressure-guard-{uid}.service'
    unit.write_text(f'''[Unit]
Description=OOM score protection for desktop UID {uid}
After=systemd-logind.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I {destination} --uid {uid}
Restart=on-failure
RestartSec=1
OOMScoreAdjust=-900
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/run
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
''')
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','--now',unit.name],check=True)
    print('커널 OOM 점수 보호 설치 완료. 관리창에서 적용 프로세스 수를 확인하세요.')
    print('systemd-oomd 보호와 메모리 용량은 별도이며 절대 생존 보장은 아닙니다.')


if __name__=='__main__': main()
