#!/usr/bin/python3
"""Root-owned, stdlib-only helper. It only adjusts OOM scores of one fixed user's PIDs."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import time


def identity(folder):
    status = (folder/'status').read_text()
    uids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
    start = (folder/'stat').read_text().rsplit(')',1)[1].split()[19]
    return tuple(map(int,uids)), start


def change(pid, uid, start, score, proc_root=Path('/proc')):
    folder = proc_root/str(pid)
    ids, actual = identity(folder)
    if not all(x == uid for x in ids) or actual != str(start):
        raise ValueError('process identity changed')
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # All subsequent accesses are tied to this proc directory, not a reused PID pathname.
        stat = os.open('stat',os.O_RDONLY,dir_fd=fd)
        with os.fdopen(stat) as file:
            if file.read().rsplit(')',1)[1].split()[19] != str(start): raise ValueError('PID reused')
        status = os.open('status',os.O_RDONLY,dir_fd=fd)
        with os.fdopen(status) as file:
            values = next(line.split()[1:] for line in file if line.startswith('Uid:'))
            if not all(int(x)==uid for x in values): raise ValueError('owner changed')
        score_fd = os.open('oom_score_adj',os.O_RDWR | os.O_NOFOLLOW,dir_fd=fd)
        with os.fdopen(score_fd,'r+') as file:
            previous=int(file.read())
            file.seek(0); file.write(str(score)); file.flush()
        return previous
    finally:
        os.close(fd)


def requests(path, uid):
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as file:
        info=os.fstat(file.fileno())
        if info.st_uid != uid or info.st_size > 256*1024 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022: raise ValueError('invalid request file')
        raw=file.read(256*1024+1)
        if len(raw)>256*1024: raise ValueError('request too large')
        data=json.loads(raw)
    if not 0 <= time.time()-data['timestamp'] <= 15: raise TimeoutError('stale controller')
    rows=data.get('processes',[])
    if not isinstance(rows,list) or len(rows)>2048: raise ValueError('too many targets')
    return [(r['pid'],str(r['start'])) for r in rows if type(r.get('pid')) is int and r['pid']>1 and str(r.get('start','')).isdigit()]


def trusted_scopes(keys, uid):
    wanted={}
    pids={pid for pid,start in keys}
    for pid,start in keys:
        try:
            group=next(line.split(':',2)[2] for line in (Path('/proc')/str(pid)/'cgroup').read_text().splitlines() if line.startswith('0::'))
            folder=(Path('/sys/fs/cgroup')/group.lstrip('/')).resolve()
            if (f'/user-{uid}.slice/' not in group or not folder.name.startswith('app-')
                    or not folder.name.endswith('.scope') or not folder.is_relative_to('/sys/fs/cgroup')): continue
            members={int(x) for x in (folder/'cgroup.procs').read_text().split()}
            if (folder.stat().st_uid==0 and members and members.issubset(pids)
                    and not any(p.is_dir() for p in folder.iterdir())):
                wanted[str(folder)]=folder
        except (OSError,ValueError,StopIteration): continue
    return wanted


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--uid',type=int,required=True); args=parser.parse_args()
    if args.uid<=0 or os.geteuid()!=0: parser.error('requires root and non-root target UID')
    path=Path('/run/user')/str(args.uid)/'probe/protection.json'
    status=Path('/run/memory-pressure-manager')/f'{args.uid}.json'
    status.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    applied={}; scope_old={}; stop=False
    def finish(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,finish); signal.signal(signal.SIGINT,finish)
    try:
        while not stop:
            try: wanted=set(requests(path,args.uid))
            except TimeoutError:
                # Keep existing protection if the unprivileged controller stalls under pressure.
                wanted=set(applied)
            except (OSError,ValueError,KeyError,StopIteration): wanted=set()
            for key,old in list(applied.items()):
                try:
                    ids,actual=identity(Path('/proc')/str(key[0]))
                    if actual!=key[1] or not all(x==args.uid for x in ids):
                        applied.pop(key,None); wanted.discard(key); continue
                except (OSError,ValueError,StopIteration):
                    applied.pop(key,None); wanted.discard(key); continue
                if key not in wanted:
                    try:
                        if int((Path('/proc')/str(key[0])/'oom_score_adj').read_text()) == -1000:
                            change(key[0],args.uid,key[1],old)
                    except (OSError,ValueError,StopIteration): pass
                    applied.pop(key,None)
            errors=0
            for pid,start in wanted:
                if (pid,start) in applied: continue
                try:
                    old=change(pid,args.uid,start,-1000)
                    if old == -1000:
                        try:
                            parent=int((Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[1])
                            inherited=next((original for (ancestor,_),original in applied.items() if ancestor==parent),None)
                            if inherited is not None: old=inherited
                        except (OSError,ValueError,IndexError): pass
                    applied[(pid,start)]=old
                except (OSError,ValueError,StopIteration): errors+=1
            scopes=trusted_scopes(set(applied),args.uid)
            for name,old in list(scope_old.items()):
                if name not in scopes:
                    try:
                        if old is None: os.removexattr(name,'user.oomd_omit')
                        else: os.setxattr(name,'user.oomd_omit',old)
                    except OSError: pass
                    scope_old.pop(name,None)
            for name in scopes:
                try:
                    if name not in scope_old:
                        try: old=os.getxattr(name,'user.oomd_omit')
                        except OSError: old=None
                        scope_old[name]=old
                    os.setxattr(name,'user.oomd_omit',b'1')
                except OSError:
                    scope_old.pop(name,None)
            temp=status.with_suffix('.tmp')
            temp.write_text(json.dumps({'timestamp':time.time(),'applied':len(applied),'errors':errors,'score':-1000, 'oomd_scopes':len(scope_old), 'protected_pids':[pid for pid,_ in applied]}))
            os.chmod(temp,0o644); os.replace(temp,status)
            time.sleep(.5)
    finally:
        for name,old in scope_old.items():
            try:
                if old is None: os.removexattr(name,'user.oomd_omit')
                else: os.setxattr(name,'user.oomd_omit',old)
            except OSError: pass
        for (pid,start),old in applied.items():
            try:
                if int((Path('/proc')/str(pid)/'oom_score_adj').read_text()) == -1000:
                    change(pid,args.uid,start,old)
            except (OSError,ValueError,StopIteration): pass
        status.unlink(missing_ok=True)


if __name__=='__main__': main()
