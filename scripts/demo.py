"""Local interview demo. Own only processes created by this launcher."""
import argparse
import fcntl
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
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'apps/mini-ad-wall/server'
CLIENT = ROOT / 'apps/mini-ad-wall/client'
STATE = ROOT / 'logs/demo'
PYTHON = str(ROOT / '.venv/bin/python')
SERVICES = {
    'agent': ('http://127.0.0.1:8000/health', 8000),
    'hosting': ('http://127.0.0.1:8002/health', 8002),
    'koa': ('http://127.0.0.1:3001/api/ads', 3001),
    'web': ('http://127.0.0.1:5173/', 5173),
}


def say(message):
    print(message, flush=True)


def fingerprint(pid):
    return subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                          capture_output=True, text=True).stdout.strip()


def records():
    path = STATE / 'processes.json'
    return json.loads(path.read_text()) if path.exists() else {}


def save(value):
    path = STATE / 'processes.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)


def alive(record):
    return bool(record.get('started')) and fingerprint(record['pid']) == record['started']


def healthy(url, token=None):
    try:
        headers = {'Accept': '*/*'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        # Local readiness checks must bypass the user's global HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(url, headers=headers), timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def occupied(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def run(name, command, env, cwd=ROOT):
    say(f'检查/准备：{name}')
    with (STATE / f'{name}.log').open('ab') as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=log)
    if result.returncode:
        raise RuntimeError(f'{name}失败，日志：{STATE / (name + ".log")}')


def spawn(name, command, env, cwd=ROOT):
    current = records()
    if name in current and alive(current[name]):
        return current[name]['pid']
    with (STATE / f'{name}.log').open('ab') as log:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True)
    started = fingerprint(proc.pid)
    if not started:
        raise RuntimeError(f'{name}启动失败，查看 {STATE / (name + ".log")}')
    current[name] = {'pid': proc.pid, 'started': started}
    save(current)
    return proc.pid


def wait_for(name, check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ready = check()
        except Exception:
            ready = False
        if ready:
            say(f'就绪：{name}')
            return
        record = records().get(name)
        if record and not alive(record):
            break
        time.sleep(.5)
    raise RuntimeError(f'{name}未就绪，查看 {STATE / (name + ".log")}')


def ensure_http(name, command, env, cwd=ROOT, token=None):
    url, port = SERVICES[name]
    if healthy(url, token):
        say(f'复用：{name}')
        return
    if occupied(port):
        raise RuntimeError(f'{port} 端口已被占用，但 {name}检查失败；请先检查原服务。')
    spawn(name, command, env, cwd)
    wait_for(name, lambda: healthy(url, token))


def environments():
    base = {**os.environ, **{k: v for k, v in dotenv_values(ROOT / '.env').items() if v is not None}}
    koa = {**base, **{k: v for k, v in dotenv_values(SERVER / '.env.local').items() if v is not None}}
    node = Path('/opt/homebrew/opt/node@22/bin/node')
    if not node.exists():
        found = shutil.which('node')
        if not found:
            raise RuntimeError('缺少 Node.js，建议安装 Node 22。')
        node = Path(found)
    version = subprocess.check_output([str(node), '--version'], text=True).strip()
    if int(version.lstrip('v').split('.')[0]) < 22:
        raise RuntimeError('需要 Node 22 或更高版本。')
    for env in (base, koa):
        env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
    for key in ('DATABASE_URL', 'REDIS_URL', 'HOSTING_CURSOR_SECRET', 'AGENT_SERVICE_TOKEN', 'OPENROUTER_API_KEY'):
        if not base.get(key):
            raise RuntimeError(f'根目录 .env 缺少 {key}')
    if not koa.get('ADS_MYSQL_URL') or not (koa.get('ADS_OPERATOR_TOKEN') or koa.get('ADS_OPERATOR_TOKENS')):
        raise RuntimeError('server/.env.local 缺少 ADS_MYSQL_URL 或运营凭据。')
    if koa.get('AGENT_SERVICE_TOKEN') != base['AGENT_SERVICE_TOKEN']:
        raise RuntimeError('根目录和 server/.env.local 的 AGENT_SERVICE_TOKEN 必须一致。')
    koa.update(HOST='127.0.0.1', PORT='3001', ADS_AGENT_API_URL='http://127.0.0.1:8000',
               AGENT_HOSTING_URL='http://127.0.0.1:8002')
    base['API_PROXY_TARGET'] = 'http://127.0.0.1:3001'
    return base, koa, str(node)


def infrastructure(env):
    from sqlalchemy import create_engine, text
    from redis import Redis
    engine = create_engine(env['DATABASE_URL'], connect_args={'connect_timeout': 3})
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        say('就绪：MySQL')
    except Exception:
        raise RuntimeError('MySQL 连接失败。请启动已配置的 MySQL；启动器不会创建或替换数据库。') from None
    finally:
        engine.dispose()
    redis = Redis.from_url(env['REDIS_URL'], socket_connect_timeout=2, socket_timeout=2)
    try:
        redis.ping()
        say('复用：Redis')
        return
    except Exception:
        pass
    url = urlparse(env['REDIS_URL'])
    port = url.port or 6379
    executable = shutil.which('redis-server')
    if url.scheme != 'redis' or url.hostname not in ('localhost', '127.0.0.1') or url.username or occupied(port) or not executable:
        raise RuntimeError('Redis 连接失败，请检查 REDIS_URL 和原服务。')
    from urllib.parse import unquote
    directory = STATE / 'redis-data'
    directory.mkdir(exist_ok=True)
    config = STATE / 'redis.conf'
    config.write_text(f'bind 127.0.0.1\nport {port}\nappendonly yes\ndir {json.dumps(str(directory))}\n'
                      + (f'requirepass {json.dumps(unquote(url.password))}\n' if url.password else ''))
    config.chmod(0o600)
    spawn('redis', [executable, str(config)], env)
    wait_for('redis', lambda: redis.ping())


def existing_worker():
    output = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True)
    for line in output.splitlines():
        if ' -m hosting.worker' not in line or 'python' not in line:
            continue
        pid = int(line.strip().split()[0])
        cwd = subprocess.run(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], capture_output=True, text=True).stdout
        if f'n{ROOT}\n' in cwd:
            return pid
    return None


def start(no_open):
    env, koa, node = environments()
    for directory in (SERVER, CLIENT):
        if not (directory / 'node_modules').exists():
            raise RuntimeError(f'{directory} 缺少依赖，请先执行 npm ci。')
    infrastructure(env)
    token = koa.get('ADS_OPERATOR_TOKEN')
    if not token and koa.get('ADS_OPERATOR_TOKENS'):
        token = next(iter(json.loads(koa['ADS_OPERATOR_TOKENS']).values()), None)
    if not healthy(SERVICES['koa'][0], token):
        run('server-build', ['npm', 'run', 'build'], koa, SERVER)
        run('ads-migration', [node, 'dist/scripts/migrate-schema.js', 'up'], koa, SERVER)
        if koa.get('MARKETING_EVENTS_ENABLED') == 'true':
            run('marketing-migration', [node, 'dist/scripts/migrate-marketing.js', 'up'], koa, SERVER)
        if koa.get('ADS_RECALL_ENABLED') == 'true':
            raise RuntimeError('演示启动器暂不管理召回进程；请单独启动召回服务，或在演示配置中关闭 ADS_RECALL_ENABLED。')
    if not healthy(SERVICES['hosting'][0]):
        run('hosting-migration', [PYTHON, '-m', 'hosting.migrate', 'up'], env)
    ensure_http('hosting', [PYTHON, '-m', 'uvicorn', 'hosting.api:app', '--host', '127.0.0.1', '--port', '8002'], env)
    ensure_http('agent', [PYTHON, str(ROOT / 'scripts/run_local_agent.py')], env)
    if existing_worker():
        say('复用：hosting Worker（进程存活）')
    else:
        spawn('worker', [PYTHON, '-m', 'hosting.worker'], env)
        time.sleep(3)
        if not alive(records()['worker']):
            raise RuntimeError(f'Worker 启动失败，查看 {STATE / "worker.log"}')
        say('就绪：hosting Worker（进程存活）')
    ensure_http('koa', [node, 'dist/index.js'], koa, SERVER, token)
    ensure_http('web', [node, str(CLIENT / 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5173', '--strictPort'], env, CLIENT)
    if not healthy('http://127.0.0.1:5173/api/ads', token):
        raise RuntimeError('前端到 Koa 的转发检查失败，请检查现有前端的 API_PROXY_TARGET。')
    if not healthy('http://127.0.0.1:5173/api/agent/identity', token):
        raise RuntimeError('Agent 身份登录检查失败，请检查 Koa 和托管服务的内部凭据。')
    say('演示地址：http://127.0.0.1:5173\n浏览器中使用 server/.env.local 的运营凭据登录。\n模型回答需要联网；Worker 存活不代表模型调用已验证。')
    if not no_open:
        subprocess.run(['open', 'http://127.0.0.1:5173'], check=True)


def stop():
    current = records()
    for name, record in reversed(list(current.items())):
        if alive(record):
            os.killpg(record['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 12
            while alive(record) and time.monotonic() < deadline:
                time.sleep(.2)
            if alive(record):
                os.killpg(record['pid'], signal.SIGKILL)
            say(f'已停止：{name}')
    save({})
    say('停止完成；原来运行的服务保留。')


def main():
    parser = argparse.ArgumentParser(description='本机面试演示：一键启动、状态检查、停止。')
    parser.add_argument('command', nargs='?', choices=['start', 'status', 'stop'], default='start')
    parser.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o700)
    with (STATE / 'launcher.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('另一个演示启动器正在执行，请稍后重试。')
        try:
            if args.command == 'stop':
                stop()
            elif args.command == 'status':
                values = dotenv_values(SERVER / '.env.local')
                token = values.get('ADS_OPERATOR_TOKEN')
                for name, (url, _) in SERVICES.items():
                    say(f'{name}: {"就绪" if healthy(url, token if name == "koa" else None) else "未就绪或需要认证"}')
                say(f'hosting Worker: {"进程存活" if existing_worker() else "未运行"}')
                say(f'日志：{STATE}')
            else:
                before = records()
                try:
                    start(args.no_open)
                except BaseException:
                    # Roll back only processes created during this attempt.
                    current = records()
                    added = {k: v for k, v in current.items() if before.get(k) != v}
                    save(added)
                    stop()
                    save(before)
                    raise
        except (Exception, KeyboardInterrupt) as exc:
            say(str(exc) if isinstance(exc, RuntimeError) else '启动/检查失败，请查看 logs/demo 日志。')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
