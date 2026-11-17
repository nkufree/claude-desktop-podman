import argparse
import hmac
import http.client
import json
import secrets
import select
import threading
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import Engine


STATIC = Path(__file__).parent / 'static'
STATIC_TYPES = {'/app.js': 'text/javascript; charset=utf-8',
                '/desktop-storage.js': 'text/javascript; charset=utf-8',
                '/style.css': 'text/css; charset=utf-8'}


class UnbufferedResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp.close()
        self.fp = sock.makefile('rb', buffering=0)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine=None):
        super().__init__(address, Handler)
        self.engine = engine or Engine()
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def reply(self, status, body, content_type='application/json; charset=utf-8', cookie=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws:; frame-src 'self'; frame-ancestors 'self'")
        if cookie:
            self.send_header('Set-Cookie', 'desktop_session=%s; HttpOnly; SameSite=Strict; Path=/' % self.server.session)
        self.end_headers()
        self.wfile.write(body)

    def guard(self, authenticated=False, mutate=False):
        port = self.server.server_port
        hosts = {'127.0.0.1:%s' % port, 'localhost:%s' % port}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host not in hosts or (origin and origin != 'http://' + host):
            self.reply(403, {'error': '拒绝跨来源请求'})
            return False
        if authenticated:
            # Edge may block cookies, and localhost cookies are shared across
            # ports. A token embedded in this same-origin page is an equivalent
            # session credential; no browser storage is required.
            token = self.headers.get('X-CSRF-Token', '') if self.path.startswith('/api/') else ''
            if self.path.startswith('/desktop/'):
                parts = self.path.split('/', 3)
                if len(parts) == 4:
                    token = parts[2]
            token_valid = token.isascii() and hmac.compare_digest(token, self.server.csrf)
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get('Cookie', ''))
                value = cookies.get('desktop_session')
                cookie_valid = value and value.value.isascii() and hmac.compare_digest(value.value, self.server.session)
                if not token_valid and not cookie_valid:
                    self.reply(401, {'error': '页面会话已失效，请刷新控制台页面；服务重启后需重新加载页面'})
                    return False
            except Exception:
                if not token_valid:
                    self.reply(401, {'error': '会话无效，请刷新控制台页面'})
                    return False
        if mutate and not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.csrf):
            self.reply(403, {'error': '请求令牌无效，请刷新页面'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.guard(authenticated=path.startswith(('/api/', '/desktop/'))):
            return
        if path == '/api/status':
            self.reply(200, self.server.engine.status())
        elif path == '/api/config':
            self.reply(200, {'config': dict(self.server.engine.config)})
        elif path.startswith('/desktop/'):
            self.proxy()
        elif path == '/':
            body = (STATIC / 'index.html').read_bytes().replace(b'__CSRF__', self.server.csrf.encode())
            self.reply(200, body, 'text/html; charset=utf-8', cookie=True)
        elif path in STATIC_TYPES:
            # Windows registry MIME associations can classify .js as text/plain.
            # With nosniff, Edge correctly refuses to execute that response.
            self.reply(200, (STATIC / path[1:]).read_bytes(), STATIC_TYPES[path])
        else:
            self.reply(404, {'error': '页面不存在'})

    def do_POST(self):
        self.close_connection = True
        if not self.guard(authenticated=True, mutate=True):
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384 or self.headers.get('Transfer-Encoding'):
                raise ValueError('请求大小无效')
            self.connection.settimeout(15)
            data = json.loads(self.rfile.read(length))
            if self.path == '/api/config':
                self.reply(200, {'config': self.server.engine.update(data)})
            elif self.path == '/api/action':
                if not isinstance(data, dict):
                    raise ValueError('请求格式错误')
                self.server.engine.submit(data.get('action'))
                self.reply(202, {'accepted': True})
            else:
                self.reply(404, {'error': '接口不存在'})
        except (ValueError, TypeError) as e:
            self.reply(400, {'error': str(e)})
        except OSError as e:
            self.reply(500, {'error': str(e)})

    def proxy(self):
        port = self.server.engine.desktop_port
        if not port:
            self.reply(503, {'error': '远程桌面尚未启动'})
            return
        target = self.path[len('/desktop'):]
        token_prefix = '/' + self.server.csrf + '/'
        if target.startswith(token_prefix):
            target = '/' + target[len(token_prefix):]
        # Prevent network-path request targets and header forwarding to arbitrary hosts.
        if not target.startswith('/') or target.startswith('//'):
            self.reply(400, {'error': '路径无效'})
            return
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ('host', 'cookie', 'authorization', 'connection',
                                        'x-csrf-token', 'referer')}
        headers['Host'] = '127.0.0.1:%s' % port
        headers['Accept-Encoding'] = 'identity'
        websocket = self.headers.get('Upgrade', '').lower() == 'websocket'
        headers['Connection'] = 'Upgrade' if websocket else 'close'
        upstream = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        # Preserve bytes sent immediately after a WebSocket 101 response.
        if websocket:
            upstream.response_class = UnbufferedResponse
        response_started = False
        try:
            upstream.request('GET', target, headers=headers)
            response = upstream.getresponse()
            body = None
            if response.status == 200 and urlsplit(target).path == '/vnc.html':
                # Debian's noVNC directly accesses localStorage. Install a
                # window-local fallback before its modules run when Edge blocks
                # site storage, including for existing container images.
                body = response.read().replace(b'<head>', b'<head><script src="/desktop-storage.js"></script>', 1)
            response_started = True
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ('transfer-encoding', 'connection', 'server', 'date',
                                       'referrer-policy', 'cache-control') and not (
                        body is not None and key.lower() in ('content-length', 'etag')):
                    self.send_header(key, value)
            if body is not None:
                self.send_header('Content-Length', str(len(body)))
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            if websocket and response.status == 101:
                self.send_header('Connection', 'Upgrade')
                self.end_headers()
                self.wfile.flush()
                self.close_connection = True
                remote = upstream.sock
                remote.settimeout(None)
                self.connection.settimeout(None)
                self.tunnel(remote)
            else:
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                if body is not None:
                    self.wfile.write(body)
                    return
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, http.client.HTTPException):
            self.close_connection = True
            if not response_started:
                self.reply(502, {'error': '远程桌面连接中断，请等待容器就绪后重新连接'})
        finally:
            upstream.close()

    def tunnel(self, remote):
        peers = [self.connection, remote]
        while True:
            readable, _, failed = select.select(peers, [], peers, 30)
            if failed:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (remote if source is self.connection else self.connection).sendall(data)


def main():
    parser = argparse.ArgumentParser(description='Claude Desktop Podman 网页控制台')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = Server(('127.0.0.1', args.port))
    except (OSError, ValueError) as e:
        parser.exit(1, '无法启动网页服务：%s\n' % e)
    url = 'http://localhost:%s' % server.server_port
    print('Claude Desktop 控制台：' + url, flush=True)
    print('Ctrl+C 关闭控制台；已启动的容器继续运行。', flush=True)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n网页服务已关闭')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
