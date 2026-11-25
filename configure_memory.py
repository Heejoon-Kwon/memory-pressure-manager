#!/usr/bin/env python3
"""Inspect or explicitly install compressed swap without disabling existing swap."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import psutil
import yaml

DEST = Path('/etc/systemd/zram-generator.conf.d/60-memory-pressure-manager.conf')


def plan(config):
    cfg = config.get('memory_optimization', {}).get('zram', {})
    percent = int(cfg.get('size_percent', 50))
    maximum = int(cfg.get('max_mib', 8192))
    algorithm = cfg.get('compression', 'zstd')
    if not 10 <= percent <= 100 or not 512 <= maximum <= 16384 or algorithm not in ('zstd','lz4','lzo-rle'):
        raise ValueError('zram 설정 범위 오류')
    existing = [p.name for p in Path('/sys/block').glob('zram*')]
    try:
        zswap = Path('/sys/module/zswap/parameters/enabled').read_text().strip() in ('Y','1')
    except OSError:
        zswap = False
    config_files = [str(p) for base in ('/etc/systemd','/usr/lib/systemd','/run/systemd')
                    for pattern in ('zram-generator.conf', 'zram-generator.conf.d/*.conf')
                    for p in Path(base).glob(pattern) if p != DEST]
    legacy = Path('/etc/default/zramswap').exists()
    text = f'[zram0]\nzram-size = min(ram * {percent} / 100, {maximum})\ncompression-algorithm = {algorithm}\nswap-priority = 100\n'
    return {'ram_mib': psutil.virtual_memory().total//1024**2, 'zram_devices':existing,
            'zswap_enabled':zswap, 'other_zram_configs':config_files, 'legacy_zram_manager':legacy,
            'can_apply':not (existing or zswap or config_files or legacy),
            'config_file':str(DEST), 'configuration':text,
            'notice':'기존 디스크 스왑은 유지합니다. 압축률·CPU 비용에 따라 효과가 달라집니다.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',default='config.yml')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text()) or {} if Path(args.config).exists() else {}
    result = plan(config)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not args.apply:
        return
    if os.geteuid() != 0:
        parser.error('적용에는 관리자 권한이 필요합니다: sudo .venv/bin/python configure_memory.py --apply')
    if not result['can_apply']:
        parser.error('기존 압축 메모리 설정을 덮어쓰지 않습니다. 위 감지 결과를 먼저 확인하세요.')
    subprocess.run(['apt-get','install','-y','systemd-zram-generator'],check=True)
    # Package installation may add policy. Refuse to override another manager.
    if not plan(config)['can_apply']:
        parser.error('설치 중 다른 zram 정책이 감지됐습니다. 설정을 덮어쓰지 않았습니다.')
    DEST.parent.mkdir(parents=True,exist_ok=True)
    if DEST.exists():
        parser.error('기존 설정 파일이 있습니다. 덮어쓰지 않습니다.')
    with DEST.open('x') as file:
        file.write(result['configuration'])
    os.chmod(DEST,0o644)
    try:
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','start','dev-zram0.swap'],check=True)
    except subprocess.CalledProcessError:
        print('zram 시작 실패. 설정 파일:', DEST, '상태: systemctl status dev-zram0.swap')
        raise
    print('압축 스왑 시작 완료. 기존 스왑은 유지됐습니다. 확인: swapon --show')


if __name__ == '__main__':
    main()
