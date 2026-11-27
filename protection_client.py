"""Publish bounded protection requests; read authoritative root-helper status."""
import os
from pathlib import Path
import time
from agents import priority


def publish(path, apps, settings, atomic_write):
    processes=[]
    ids={p.pid for app in apps if app.managed and priority(app.key,settings)==0 for p in app.processes}
    ids.add(os.getpid())
    for pid in sorted(ids):
        try:
            start=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
            processes.append({'pid':pid,'start':start})
        except (OSError,IndexError): continue
    atomic_write(path,{'timestamp':time.time(),'processes':processes[:2048]})


def status():
    import json
    try:
        path=Path('/run/memory-pressure-manager')/f'{os.getuid()}.json'
        st=path.stat()
        value=json.loads(path.read_text())
        if st.st_uid!=0 or st.st_mode & 0o022 or not 0 <= time.time()-value['timestamp'] <= 5:
            raise ValueError('untrusted or stale helper')
        value['installed'] = True
        return value
    except (OSError,ValueError,KeyError):
        return {'applied':0,'oomd_scopes':0,'installed':False}
