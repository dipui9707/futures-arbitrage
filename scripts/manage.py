#!/usr/bin/env python3
"""Portable local-only lifecycle controller; never manages another project's process."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import fcntl

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime'
RUNTIME.mkdir(exist_ok=True)
PYTHON = ROOT / '.venv/bin/python'
STATE = RUNTIME / 'runner.json'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def settings():
    env = os.environ.copy()
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=',1)
                env.setdefault(key.strip(),value.strip().strip('"').strip("'"))
    env.setdefault('DATA_SOURCE','mock')
    env.setdefault('HOST','127.0.0.1')
    env.setdefault('FRONTEND_PORT','5173')
    env.setdefault('BACKEND_PORT','8767')
    env.setdefault('DATABASE_PATH','data/arbitrage.sqlite3')
    for key in ('FRONTEND_PORT','BACKEND_PORT'):
        if not env[key].isdigit() or not 1024 <= int(env[key]) <= 65535:
            raise SystemExit(f'{key} must be an integer from 1024 to 65535')
    if env['FRONTEND_PORT']==env['BACKEND_PORT']:
        raise SystemExit('Frontend and backend ports must differ')
    return env

def fingerprint():
    return hashlib.sha256((ROOT/'frontend/package-lock.json').read_bytes()+(ROOT/'backend/requirements.lock').read_bytes()).hexdigest()

def setup():
    if not shutil.which('node') or not shutil.which('npm'):
        raise SystemExit('Install Node.js 22.13+ (22 LTS recommended) and npm first.')
    major,minor = map(int,subprocess.check_output(['node','--version'],text=True).strip().lstrip('v').split('.')[:2])
    if (major,minor)<(22,13):
        raise SystemExit('Node.js 22.13 or newer is required.')
    if sys.version_info<(3,11):
        raise SystemExit('Python 3.11 or newer is required.')
    if not (ROOT/'.env').exists():
        shutil.copyfile(ROOT/'.env.example',ROOT/'.env')
    marker=RUNTIME/'dependencies.sha256'
    digest=fingerprint()
    if PYTHON.exists() and (ROOT/'frontend/node_modules/.bin/vinext').exists() and marker.exists() and marker.read_text()==digest:
        return
    if not PYTHON.exists():
        subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True)
    subprocess.run([str(PYTHON),'-m','pip','install','-r','backend/requirements.lock'],cwd=ROOT,check=True)
    subprocess.run(['npm','ci'],cwd=ROOT/'frontend',check=True)
    marker.write_text(digest)

def process_state():
    try:
        state=json.loads(STATE.read_text())
        command=subprocess.check_output(['ps','-p',str(state['pid']),'-o','command='],text=True).strip()
        return state if str(Path(__file__).resolve())+' serve' in command else None
    except (OSError,ValueError,KeyError,subprocess.CalledProcessError):
        return None

def healthy(url):
    try:
        with OPENER.open(url,timeout=4) as r:
            return r.status==200
    except Exception:
        return False

def stop():
    state=process_state()
    if state is None:
        print('This project is not running.')
        STATE.unlink(missing_ok=True)
        return
    os.kill(state['pid'],signal.SIGTERM)
    for _ in range(40):
        if process_state() is None:
            STATE.unlink(missing_ok=True)
            print('Stopped this project.')
            return
        time.sleep(.25)
    raise SystemExit('Shutdown is still pending; inspect .runtime/supervisor.log before retrying.')

def start():
    state=process_state()
    if state:
        print(f"Already running: {state['url']}")
        return
    setup()
    env=settings()
    host=env['HOST']
    if host not in ('127.0.0.1','localhost'):
        raise SystemExit('This MVP starts on loopback only. LAN/public deployment requires the next-stage proxy and authentication setup.')
    for name in ('FRONTEND_PORT','BACKEND_PORT'):
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:
                sock.bind(('127.0.0.1',int(env[name])))
            except OSError:
                raise SystemExit(f"{name}={env[name]} is occupied. Change this project's .env; no existing process was stopped.")
    url=f"http://127.0.0.1:{env['FRONTEND_PORT']}/"
    with (RUNTIME/'supervisor.log').open('a') as log:
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'serve'],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    STATE.write_text(json.dumps({'pid':child.pid,'url':url,'backend_port':env['BACKEND_PORT']}))
    deadline=time.time()+55
    while time.time()<deadline:
        if child.poll() is not None:
            STATE.unlink(missing_ok=True)
            raise SystemExit('Startup failed; see .runtime/backend.log and .runtime/frontend.log.')
        if healthy(url+'api/health') and healthy(url):
            print(f'Ready: {url}\nStop: npm stop\nLogs: .runtime/')
            return
        time.sleep(.5)
    stop()
    raise SystemExit('Startup timed out; inspect .runtime/ logs.')

def serve():
    env=settings()
    # Keep lifecycle status accurate when launchd owns this foreground process.
    # start() also writes the same state after spawning us; both paths converge
    # on the supervisor PID and the same loopback URL.
    STATE.write_text(json.dumps({
        'pid':os.getpid(),
        'url':f"http://127.0.0.1:{env['FRONTEND_PORT']}/",
        'backend_port':env['BACKEND_PORT'],
    }))
    children=[]
    running=True
    def finish(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,finish)
    signal.signal(signal.SIGINT,finish)
    try:
        commands=[('backend',[str(PYTHON),'-m','uvicorn','app.main:app','--app-dir',str(ROOT/'backend'),'--host','127.0.0.1','--port',env['BACKEND_PORT']],ROOT),
                  ('frontend',['npm','run','dev','--','--host','127.0.0.1','--port',env['FRONTEND_PORT']],ROOT/'frontend')]
        for name,command,cwd in commands:
            with (RUNTIME/f'{name}.log').open('a') as log:
                children.append(subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True))
        while running and all(c.poll() is None for c in children):
            time.sleep(.3)
    finally:
        for c in children:
            if c.poll() is None:
                os.killpg(c.pid,signal.SIGTERM)
        for c in children:
            try:
                c.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(c.pid,signal.SIGKILL)
                c.wait()
        try:
            state=json.loads(STATE.read_text())
            if state.get('pid')==os.getpid():
                STATE.unlink()
        except (OSError,ValueError,KeyError):
            pass

def check():
    setup()
    subprocess.run([str(PYTHON),'-m','unittest','discover','-s','backend/tests','-v'],cwd=ROOT,check=True)
    subprocess.run(['npm','run','typecheck'],cwd=ROOT/'frontend',check=True)
    subprocess.run(['npm','run','lint'],cwd=ROOT/'frontend',check=True)
    subprocess.run(['npm','run','build'],cwd=ROOT/'frontend',check=True)

if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)>1 else 'start'
    if action=='serve':
        serve()
    else:
        # Serialize this project's start/stop/setup so parallel commands cannot
        # create duplicate workers or overwrite a lifecycle state file.
        with (RUNTIME/'lifecycle.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if action=='status':
                state=process_state()
                print(json.dumps({'running':bool(state),'url':state['url'] if state else None,'healthy':healthy(state['url']+'api/health') if state else False}))
            elif action in ('start','stop','setup','check'):
                globals()[action]()
            else:
                raise SystemExit('Usage: python3 scripts/manage.py start|stop|status|setup|check')
