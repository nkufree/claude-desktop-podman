import collections
import datetime
import json
import os
import platform
import shutil
import shlex
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, build_opener

from . import config


OWNER = 'io.claude-desktop.workspace'
NAME = 'claude-desktop-' + config.IDENTITY
IMAGE = 'localhost/claude-desktop-web:' + config.IDENTITY
VOLUME = NAME + '-home'
HOME_DIRECTORY = Path.home() / '.claude_podman' / 'desktop-home'


def podman_binary():
    candidates = [shutil.which('podman'), '/opt/homebrew/bin/podman',
                  '/usr/local/bin/podman', str(Path.home() / '.local/homebrew/bin/podman')]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def brew_binary():
    candidates = [shutil.which('brew'), '/opt/homebrew/bin/brew', '/usr/local/bin/brew',
                  str(Path.home() / '.local/homebrew/bin/brew')]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def podman_environment(env=None):
    result = dict(os.environ if env is None else env)
    binary = podman_binary()
    if binary:
        # Homebrew installs krunkit/gvproxy beside podman. GUI-launched shells
        # may not have loaded brew shellenv even when we found podman by path.
        result['PATH'] = str(Path(binary).parent) + os.pathsep + result.get('PATH', '')
    return result


def container_proxy(value):
    """Host loopback is different from container/VM loopback."""
    if not value:
        return ''
    u = urlsplit(value)
    host = 'host.containers.internal' if u.hostname in ('localhost', '127.0.0.1', '::1') else u.hostname
    if ':' in host:
        host = '[' + host + ']'
    return urlunsplit((u.scheme, '%s:%s' % (host, u.port), '', '', ''))


class Engine:
    def __init__(self, on_log=None):
        self.lock = threading.RLock()
        self.logs = collections.deque(maxlen=600)
        self.on_log = on_log
        self.busy = False
        self.operation = ''
        self.error = ''
        self.desktop_port = None
        self.config = config.load()
        saved_path = config.STATE / 'saved-image.json'
        self.saved_image = json.loads(saved_path.read_text()) if saved_path.exists() else None
        origin_path = config.STATE / 'image-origin.json'
        self.image_origin = json.loads(origin_path.read_text()) if origin_path.exists() else {'source': 'build', 'reference': ''}
        self._probe_cache = (0, {})

    def log(self, message):
        with self.lock:
            for line in str(message).splitlines():
                self.logs.append({'time': time.strftime('%H:%M:%S'), 'message': line[-3000:]})
                if self.on_log:
                    self.on_log(line)

    def run(self, args, timeout=30, check=True):
        binary = podman_binary()
        if not binary:
            raise RuntimeError('未安装 Podman。macOS 可执行 brew install podman')
        proc = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout,
                              env=podman_environment())
        if check and proc.returncode:
            raise RuntimeError((proc.stderr or proc.stdout or 'Podman 命令失败').strip()[-5000:])
        return proc

    def inspect(self):
        proc = self.run(['container', 'inspect', NAME], check=False)
        if proc.returncode:
            return None
        data = json.loads(proc.stdout)[0]
        if data.get('Config', {}).get('Labels', {}).get(OWNER) != config.IDENTITY:
            raise RuntimeError('同名容器不属于此项目，拒绝操作：' + NAME)
        return data

    def probe(self):
        now = time.monotonic()
        if now - self._probe_cache[0] < 8:
            return self._probe_cache[1]
        result = {'installed': bool(podman_binary()), 'connected': False,
                  'version': '', 'message': '', 'platform': platform.system()}
        if result['installed']:
            try:
                p = self.run(['info', '--format', 'json'], timeout=12)
                info = json.loads(p.stdout)
                result.update(connected=True, version=info.get('version', {}).get('Version', ''))
            except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
                result['message'] = str(e)
        else:
            result['message'] = '请先安装 Podman；macOS：brew install podman'
        self._probe_cache = (now, result)
        return result

    def desktop(self, data):
        if not data or not data.get('State', {}).get('Running'):
            self.desktop_port = None
            return False
        ports = data.get('NetworkSettings', {}).get('Ports', {}).get('6080/tcp') or []
        self.desktop_port = int(ports[0]['HostPort']) if ports else None
        if not self.desktop_port:
            return False
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open('http://127.0.0.1:%s/vnc.html' % self.desktop_port, timeout=1) as r:
                return r.status == 200
        except OSError:
            return False

    def status(self):
        env = self.probe()
        state, ready = 'stopped', False
        runtime_error = ''
        if env['connected']:
            try:
                data = self.inspect()
                ready = self.desktop(data)
                if data and data.get('State', {}).get('Running'):
                    state = 'running' if ready else 'starting'
                elif data:
                    state = 'exited'
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
                runtime_error = str(e)
        with self.lock:
            return {'environment': env, 'state': state, 'ready': ready,
                    'busy': self.busy, 'operation': self.operation,
                    'error': self.error or runtime_error, 'config': dict(self.config),
                    'logs': list(self.logs), 'container': NAME, 'image': IMAGE, 'volume': VOLUME,
                    'home_path': str(HOME_DIRECTORY),
                    'image_origin': self.image_origin,
                    'saved_image': self.saved_image}

    def update(self, values):
        with self.lock:
            if self.busy:
                raise ValueError('操作进行中，请稍后修改配置')
            values = config.validate(values)
            config.save(values)
            self.config = values
        return values

    def submit(self, action):
        if action not in ('start', 'stop', 'rebuild', 'machine', 'setup', 'logs', 'save'):
            raise ValueError('未知操作')
        with self.lock:
            if self.busy:
                raise ValueError('已有操作正在进行')
            self.busy, self.operation, self.error = True, action, ''
        threading.Thread(target=self.work, args=(action,), daemon=True).start()

    def work(self, action):
        try:
            getattr(self, 'do_' + action)()
        except Exception as e:
            with self.lock:
                self.error = str(e)
            self.log('错误：' + str(e))
        finally:
            with self.lock:
                self.busy, self.operation = False, ''
                self._probe_cache = (0, {})

    def stream(self, args, timeout=1800, env=None):
        binary = podman_binary()
        if not binary:
            raise RuntimeError('请先安装 Podman')
        self.stream_command([binary] + args, timeout=timeout, env=env)

    def stream_command(self, command, timeout=1800, env=None):
        # Drain output in a separate thread so a stalled pull/build also times out.
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, env=podman_environment(env))
        def drain():
            for line in proc.stdout:
                self.log(line.rstrip())
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise RuntimeError('环境命令执行超时，请查看日志并重试')
        finally:
            reader.join(timeout=3)
            proc.stdout.close()
        if code:
            raise RuntimeError('环境命令执行失败（退出码 %s），请查看日志' % code)

    def do_setup(self):
        if not podman_binary():
            if platform.system() != 'Darwin':
                raise RuntimeError('请使用 Linux 发行版包管理器安装 Podman 后重试')
            brew = brew_binary()
            if not brew:
                raise RuntimeError('请先从 https://brew.sh 安装 Homebrew，再点击一键配置')
            env = os.environ.copy()
            env['HOMEBREW_NO_AUTO_UPDATE'] = '1'
            if self.config['proxy']:
                for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
                    env[key] = self.config['proxy']
            self.log('使用 Homebrew 安装 Podman…')
            self.stream_command([brew, 'install', 'podman'], env=env)
        if platform.system() == 'Darwin':
            self.do_machine()
            self.prepare_machine_proxy()
        self._probe_cache = (0, {})
        if not self.probe()['connected']:
            raise RuntimeError('Podman 连接失败，请查看引擎检测详情')
        self.log('Podman 环境配置完成，可以启动桌面。')

    def do_machine(self):
        if platform.system() != 'Darwin':
            raise RuntimeError('Linux 请直接启动 Podman；此按钮用于 macOS Podman machine')
        machines = json.loads(self.run(['machine', 'list', '--format', 'json']).stdout)
        env = os.environ.copy()
        if self.config['proxy']:
            for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
                env[key] = self.config['proxy']
            env['NO_PROXY'] = env['no_proxy'] = 'localhost,127.0.0.1,::1'
        default = next((m for m in machines if m.get('Default')), machines[0] if machines else None)
        if default and default.get('Running'):
            self.log('Podman 虚拟机已在运行')
            return
        if default:
            name = default['Name']
        else:
            name = 'podman-machine-default'
            self.stream(['machine', 'init', '--cpus', '4', '--memory', '6144', name], env=env)
        self.stream(['machine', 'start', name], timeout=300, env=env)
        self.log('Podman 虚拟机已启动')

    def build(self):
        c = self.config
        if platform.system() == 'Darwin':
            self.prepare_machine_proxy()
        args = ['build', '--layers', '--file', str(config.ROOT / 'desktop/Containerfile'), '--tag', IMAGE]
        for key, value in [('APT_MIRROR', c['apt_mirror']), ('CLAUDE_RELEASE', c['release']), ('CLAUDE_DEB_URL', c['package_url']),
                           ('CLAUDE_DEB_SHA256', c['package_sha256']),
                           ('CLAUDE_INSTALL_REV', str(time.time_ns()))]:
            args += ['--build-arg', key + '=' + value]
        env = os.environ.copy()
        if c['proxy']:
            # Remote Podman builds run in the VM; explicitly supply a container-reachable proxy.
            for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
                args += ['--build-arg', key + '=' + container_proxy(c['proxy'])]
                env[key] = c['proxy']
        args += [str(config.ROOT / 'desktop')]
        self.log('构建 Debian / Xfce / Claude Desktop 镜像，首次下载可能需要数分钟…')
        self.stream(args, env=env)
        self.record_image({'source': 'build', 'reference': ''})

    def record_image(self, origin):
        config.STATE.mkdir(mode=0o700, exist_ok=True)
        path = config.STATE / 'image-origin.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(origin) + '\n')
        os.chmod(temp, 0o600)
        temp.replace(path)
        with self.lock:
            self.image_origin = origin
        if self.saved_image:
            (config.STATE / 'saved-image.json').unlink(missing_ok=True)
            with self.lock:
                self.saved_image = None
            self.log('已更新启动镜像；以前保存的具名镜像仍保留在 Podman 中。')

    def pull(self):
        if platform.system() == 'Darwin':
            self.prepare_machine_proxy()
        reference = self.config['image_ref']
        env = os.environ.copy()
        if self.config['proxy']:
            for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
                env[key] = self.config['proxy']
        self.log('拉取预构建桌面镜像（Podman 自动选择当前系统架构）：' + reference)
        self.stream(['pull', reference], env=env)
        # Do not replace the startup image or container until the pull succeeds.
        self.run(['tag', reference, IMAGE])
        self.record_image({'source': 'pull', 'reference': reference})

    def prepare_machine_proxy(self):
        """Registry pulls happen in the VM service, before build-args apply."""
        machines = json.loads(self.run(['machine', 'list', '--format', 'json']).stdout)
        machine = next((m for m in machines if m.get('Default') and m.get('Running')), None)
        if not machine:
            machine = next((m for m in machines if m.get('Running')), None)
        if not machine:
            raise RuntimeError('请先启动 Podman 虚拟机')
        name = machine['Name']
        details = json.loads(self.run(['machine', 'inspect', name]).stdout)[0]
        rootful = details.get('Rootful', False)
        systemctl = ['systemctl'] + ([] if rootful else ['--user'])
        proxy = container_proxy(self.config['proxy'])
        values = [key + '=' + proxy for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')]
        values += ['NO_PROXY=localhost,127.0.0.1,::1', 'no_proxy=localhost,127.0.0.1,::1']
        # Quote each value: the command is interpreted by the VM's SSH shell.
        command = shlex.join(systemctl + ['set-environment'] + values)
        command += ' && ' + shlex.join(systemctl + ['restart', 'podman.service'])
        args = ['machine', 'ssh'] + (['--username', 'root'] if rootful else []) + [name, command]
        self.log('将配置的代理应用到 Podman 虚拟机的镜像服务…')
        self.run(args, timeout=30)

    def do_start(self, rebuild=False):
        if not self.probe()['connected']:
            raise RuntimeError('Podman 尚未就绪，请安装并启动 Podman / Podman machine')
        old = self.inspect()  # Verify ownership before building/removing anything.
        for mount in self.config['mounts']:
            if not Path(mount['source']).is_dir():
                raise RuntimeError('挂载目录不存在，请检查网页配置：' + mount['source'])
        origin = {'source': self.config['image_source'],
                  'reference': self.config['image_ref'] if self.config['image_source'] == 'pull' else ''}
        if rebuild or self.image_origin != origin or self.run(['image', 'exists', IMAGE], check=False).returncode:
            if self.config['image_source'] == 'pull':
                self.pull()
            else:
                self.build()
        HOME_DIRECTORY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        HOME_DIRECTORY.mkdir(mode=0o700, exist_ok=True)
        if old and not any(HOME_DIRECTORY.iterdir()):
            mounts = old.get('Mounts', [])
            previous_home = next((m for m in mounts if m.get('Destination') == '/home/claude'), None)
            if previous_home and previous_home.get('Source') != str(HOME_DIRECTORY):
                self.log('将原容器 home 数据迁移到宿主机 ' + str(HOME_DIRECTORY))
                if old.get('State', {}).get('Running'):
                    self.run(['stop', '--time', '15', NAME], timeout=30)
                self.run(['cp', '--archive=false', NAME + ':/home/claude/.', str(HOME_DIRECTORY)], timeout=120)
        if old:
            self.run(['rm', '--force', NAME])
        c = self.config
        # Keep this runtime copy in the home mount, outside saved images and
        # Podman process arguments / container environment metadata.
        password_dir = HOME_DIRECTORY / '.config' / 'claude-podman'
        password_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        password_path = password_dir / 'keyring-password'
        temp_password = password_path.with_suffix('.tmp')
        fd = os.open(str(temp_password), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write(c['keyring_password'])
            temp_password.replace(password_path)
        finally:
            temp_password.unlink(missing_ok=True)
        args = ['run', '--detach', '--name', NAME, '--label', OWNER + '=' + config.IDENTITY,
                '--publish', '127.0.0.1::6080', '--cpus', str(c['cpus']),
                '--memory', '%sm' % c['memory'], '--shm-size', '512m',
                '--cap-drop=all', '--security-opt=no-new-privileges',
                '--userns=keep-id:uid=1000,gid=1000',
                '--mount', 'type=bind,source=%s,target=/home/claude' % HOME_DIRECTORY,
                '--env', 'DESKTOP_RESOLUTION=' + c['resolution'], '--env', 'TZ=' + c['timezone']]
        # A committed image can inherit an older container's proxy environment;
        # explicitly clear it when the user removes the proxy setting.
        proxy = container_proxy(c['proxy'])
        for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
            args += ['--env', key + '=' + proxy]
        args += ['--env', 'DESKTOP_PROXY=' + proxy, '--env', 'no_proxy=localhost,127.0.0.1,::1']
        for mount in c['mounts']:
            value = 'type=bind,source=%s,target=%s' % (mount['source'], mount['target'])
            if mount['readonly']:
                value += ',readonly=true'
            args += ['--mount', value]
        for mapping in c['ports']:
            args += ['--publish', '127.0.0.1:%s:%s/%s' % (mapping['host'], mapping['container'], mapping['protocol'])]
        args += [IMAGE]
        self.log('创建桌面容器；Claude 凭据与桌面资料保存在宿主机 ' + str(HOME_DIRECTORY))
        self.run(args, timeout=120)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            data = self.inspect()
            if not data or not data.get('State', {}).get('Running'):
                self.do_logs()
                raise RuntimeError('桌面容器提前退出，请查看日志')
            if self.desktop(data):
                self.log('远程桌面已就绪，可在容器浏览器中登录 Claude')
                return
            time.sleep(1)
        self.do_logs()
        raise RuntimeError('容器已启动，但远程桌面未在 90 秒内就绪')

    def do_rebuild(self):
        self.do_start(rebuild=True)

    def do_stop(self):
        if self.inspect():
            self.run(['stop', '--time', '15', NAME], timeout=30)
        self.desktop_port = None
        self.log('容器已停止，登录数据已保留')

    def do_save(self):
        if not self.inspect():
            raise RuntimeError('尚无可保存的容器，请先启动桌面')
        tag = 'localhost/claude-desktop-saved:%s-%s' % (config.IDENTITY, time.time_ns())
        self.log('保存容器文件系统为镜像；运行中的容器会短暂暂停，挂载卷不包含在镜像中。')
        self.stream(['commit', '--pause=true', NAME, tag], timeout=600)
        self.run(['tag', tag, IMAGE])
        saved = {'image': tag, 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        config.STATE.mkdir(mode=0o700, exist_ok=True)
        path = config.STATE / 'saved-image.json'
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(saved, indent=2) + '\n')
        os.chmod(temp, 0o600)
        temp.replace(path)
        with self.lock:
            self.saved_image = saved
        self.log('容器已保存：' + tag)
        self.log('下次启动使用此镜像，继续挂载原数据卷。更换镜像来源、重建或重新拉取会替换启动镜像。')

    def do_logs(self):
        data = self.inspect()
        if data:
            result = self.run(['logs', '--tail', '100', NAME])
            self.log(result.stdout + result.stderr)
            if data.get('State', {}).get('Running'):
                result = self.run(['exec', NAME, 'tail', '-n', '80',
                                  '/home/claude/.cache/claude-desktop-debian/launcher.log'], check=False)
                if not result.returncode:
                    self.log('--- Claude Desktop launcher ---')
                    self.log(result.stdout)
