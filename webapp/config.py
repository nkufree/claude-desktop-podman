import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.desktop-state'
IDENTITY = hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
DEFAULTS = {
    'cpus': 2, 'memory': 4096, 'resolution': '1920x1080',
    'timezone': 'America/New_York', 'release': 'latest',
    'package_url': '', 'package_sha256': '', 'proxy': '',
    'apt_mirror': 'http://deb.debian.org',
    'keyring_password': 'fuckclaude',
    'image_source': 'build',
    'image_ref': 'ghcr.io/nkufree/claude-desktop-podman/claude-desktop:latest',
    'mounts': [],
    'ports': [],
}


def validate(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError('配置包含未知字段')
    result = dict(DEFAULTS, **data)
    for key, low, high in [('cpus', 1, 32), ('memory', 2048, 65536)]:
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError('%s 必须在 %s–%s 之间' % (key, low, high))
    for key in set(DEFAULTS) - {'cpus', 'memory', 'mounts', 'ports'}:
        if not isinstance(result[key], str) or len(result[key]) > 2048:
            raise ValueError('%s 格式错误' % key)
        if key != 'keyring_password':
            result[key] = result[key].strip()
    password = result['keyring_password']
    if not password or len(password) > 256 or any(c in password for c in '\x00\r\n'):
        raise ValueError('密钥环密码必须为 1–256 个字符，不能包含换行或空字符')
    if result['image_source'] not in ('build', 'pull'):
        raise ValueError('镜像来源必须为自行构建或预构建镜像')
    image_pattern = (r'(?:localhost|[a-z0-9][a-z0-9.\-]*[a-z0-9])(?::[0-9]{1,5})?/'
                     r'[a-z0-9]+(?:[._\-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._\-]+[a-z0-9]+)*)*'
                     r'(?::[A-Za-z0-9_][A-Za-z0-9_.\-]{0,127}|@sha256:[a-fA-F0-9]{64})?')
    if not re.fullmatch(image_pattern, result['image_ref']):
        raise ValueError('请填写完整镜像地址，例如 ghcr.io/nkufree/claude-desktop-podman/claude-desktop:latest，可固定标签或 SHA256 摘要')
    if result['resolution'] not in ('1280x800', '1440x900', '1600x1000', '1920x1080'):
        raise ValueError('请选择提供的桌面分辨率')
    tz = result['timezone']
    if not re.fullmatch(r'[A-Za-z0-9_+\-/]+', tz) or '..' in tz or tz.startswith('/'):
        raise ValueError('时区格式错误')
    if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,100}', result['release']):
        raise ValueError('版本号格式错误')
    url = result['package_url']
    if url and (urlsplit(url).scheme != 'https' or not urlsplit(url).hostname or urlsplit(url).username):
        raise ValueError('安装包地址必须是无用户名密码的 HTTPS URL')
    digest = result['package_sha256']
    if digest and not re.fullmatch(r'[a-fA-F0-9]{64}', digest):
        raise ValueError('SHA256 必须为 64 位十六进制字符')
    proxy = result['proxy']
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if parsed.scheme != 'http' or not parsed.hostname or not parsed.port or parsed.username or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
                raise ValueError()
        except ValueError:
            raise ValueError('代理请使用 http://主机:端口，不包含认证信息')
    mirror = urlsplit(result['apt_mirror'])
    if mirror.scheme != 'http' or not mirror.hostname or mirror.username or mirror.path not in ('', '/') or mirror.query or mirror.fragment or not re.fullmatch(r'[A-Za-z0-9.\-]+(?::[0-9]+)?', mirror.netloc):
        raise ValueError('Debian 软件源请填写 HTTP 站点根地址，不包含 /debian 路径；APT 会验证 Debian 签名')
    result['apt_mirror'] = result['apt_mirror'].rstrip('/')
    mounts = result['mounts']
    if not isinstance(mounts, list) or len(mounts) > 20:
        raise ValueError('挂载目录最多 20 个')
    checked, targets = [], []
    for mount in mounts:
        if not isinstance(mount, dict) or set(mount) != {'source', 'target', 'readonly'}:
            raise ValueError('挂载配置格式错误')
        source, target = mount['source'], mount['target']
        if any(not isinstance(p, str) or not p or len(p) > 2048 or any(c in p for c in '\x00\n\r,') for p in (source, target)):
            raise ValueError('挂载路径格式错误，不能包含逗号或换行')
        if not Path(source).is_absolute():
            raise ValueError('宿主机挂载路径必须是绝对目录：' + source)
        destination = PurePosixPath(target)
        if not destination.is_absolute() or '..' in destination.parts or destination == PurePosixPath('/'):
            raise ValueError('容器挂载路径必须是绝对目录，不能是根目录')
        target = str(destination)
        for reserved in ('/home/claude', '/proc', '/sys', '/dev', '/run'):
            protected = PurePosixPath(reserved)
            if destination == protected or protected in destination.parents or destination in protected.parents:
                raise ValueError('挂载路径不能覆盖默认 home 卷或系统运行目录：' + target)
        if target in targets or type(mount['readonly']) is not bool:
            raise ValueError('容器挂载路径不能重复；只读设置必须为布尔值')
        targets.append(target)
        checked.append({'source': str(Path(source).resolve()), 'target': target, 'readonly': mount['readonly']})
    result['mounts'] = checked
    ports = result['ports']
    if not isinstance(ports, list) or len(ports) > 20:
        raise ValueError('端口映射最多 20 个')
    checked_ports, published = [], set()
    for mapping in ports:
        if not isinstance(mapping, dict) or set(mapping) != {'host', 'container', 'protocol'}:
            raise ValueError('端口映射格式错误')
        if any(type(mapping[key]) is not int or not 1 <= mapping[key] <= 65535 for key in ('host', 'container')):
            raise ValueError('端口必须是 1–65535 之间的整数')
        if mapping['protocol'] not in ('tcp', 'udp'):
            raise ValueError('端口协议必须是 TCP 或 UDP')
        endpoint = (mapping['host'], mapping['protocol'])
        if endpoint in published:
            raise ValueError('同一协议的宿主机端口不能重复')
        published.add(endpoint)
        checked_ports.append(dict(mapping))
    result['ports'] = checked_ports
    return result


def load():
    path = STATE / 'config.json'
    if path.exists():
        return validate(json.loads(path.read_text()))
    values = dict(DEFAULTS)
    values['proxy'] = system_proxy()
    return values


def system_proxy():
    for key in ('https_proxy', 'HTTPS_PROXY', 'http_proxy', 'HTTP_PROXY'):
        value = os.environ.get(key, '')
        if value:
            try:
                return validate({'proxy': value})['proxy']
            except ValueError:
                pass
    if sys.platform == 'darwin':
        try:
            text = subprocess.check_output(['/usr/sbin/scutil', '--proxy'], text=True, timeout=3)
            if re.search(r'HTTPSEnable\s*:\s*1', text):
                host = re.search(r'HTTPSProxy\s*:\s*(\S+)', text)
                port = re.search(r'HTTPSPort\s*:\s*(\d+)', text)
                if host and port:
                    return validate({'proxy': 'http://%s:%s' % (host[1], port[1])})['proxy']
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return ''


def save(config):
    STATE.mkdir(mode=0o700, exist_ok=True)
    temp = STATE / 'config.json.tmp'
    temp.write_text(json.dumps(validate(config), ensure_ascii=False, indent=2) + '\n')
    os.chmod(temp, 0o600)
    temp.replace(STATE / 'config.json')
