import contextlib
import http.client
import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp import config
from webapp.engine import Engine, NAME, OWNER, container_proxy
from webapp.server import Server


class ConfigTests(unittest.TestCase):
    def test_rejects_invalid_settings(self):
        for bad in ({'cpus': True}, {'memory': 1}, {'cpus': '2'}, {'unknown': 1},
                    {'resolution': '12x34'}, {'timezone': '../../etc'},
                    {'release': 'a/b'}, {'package_url': 'file:///etc/passwd'},
                    {'proxy': 'http://user:password@localhost:1234'},
                    {'apt_mirror': 'file:///tmp'}, {'apt_mirror': 'http://mirror.example/debian'},
                    {'package_sha256': 'invalid'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.validate(bad)

    def test_save_load_and_permissions(self):
        with tempfile.TemporaryDirectory() as path, patch.object(config, 'STATE', Path(path)):
            values = config.validate({'cpus': 4, 'proxy': 'http://127.0.0.1:10808'})
            config.save(values)
            self.assertEqual(config.load(), values)
            self.assertEqual((Path(path) / 'config.json').stat().st_mode & 0o777, 0o600)

    def test_loopback_proxy_rewrite(self):
        self.assertEqual(container_proxy('http://127.0.0.1:10808'), 'http://host.containers.internal:10808')
        self.assertEqual(container_proxy('http://proxy.lan:8080'), 'http://proxy.lan:8080')
        self.assertEqual(container_proxy(''), '')

    def test_keyring_password_validation_preserves_spaces(self):
        self.assertEqual(config.validate({})['keyring_password'], 'fuckclaude')
        self.assertEqual(config.validate({'keyring_password': ' custom password '})['keyring_password'], ' custom password ')
        for bad in ('', 'x\ny', 'x\x00y', 'x' * 257, 123):
            with self.subTest(password_type=type(bad)), self.assertRaises(ValueError):
                config.validate({'keyring_password': bad})

    def test_mount_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as path, patch.object(config, 'STATE', Path(path)):
            mount = {'source': path, 'target': '/workspace', 'readonly': True}
            values = config.validate({'mounts': [mount]})
            config.save(values)
            self.assertEqual(config.load()['mounts'], [dict(mount, source=str(Path(path).resolve()))])
            for bad in (dict(mount, source='relative'), dict(mount, target='/home/claude/files'),
                        dict(mount, target='/'), dict(mount, target='/sys'),
                        dict(mount, target='/workspace/../home'), dict(mount, readonly='true'),
                        dict(mount, source='/tmp/a,b')):
                with self.subTest(mount=bad), self.assertRaises(ValueError):
                    config.validate({'mounts': [bad]})
            with self.assertRaises(ValueError):
                config.validate({'mounts': [mount, mount]})

    def test_port_validation_and_persistence(self):
        mapping = {'host': 18080, 'container': 8080, 'protocol': 'tcp'}
        with tempfile.TemporaryDirectory() as path, patch.object(config, 'STATE', Path(path)):
            values = config.validate({'ports': [mapping, dict(mapping, protocol='udp')]})
            config.save(values)
            self.assertEqual(config.load()['ports'], values['ports'])
        for bad in (dict(mapping, host=0), dict(mapping, host=True), dict(mapping, container=65536),
                    dict(mapping, protocol='http'), dict(mapping, host='18080')):
            with self.subTest(mapping=bad), self.assertRaises(ValueError):
                config.validate({'ports': [bad]})
        with self.assertRaises(ValueError):
            config.validate({'ports': [mapping, mapping]})

    def test_architecture_package_selection(self):
        spec = importlib.util.spec_from_file_location('installer', config.ROOT / 'desktop/install-claude.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        assets = [{'name': name} for name in ['claude-desktop_1_all.deb', 'claude-desktop_2_arm64.deb',
                  'claude-desktop-unofficial_2_arm64.deb', 'claude-desktop_2_amd64.deb']]
        self.assertEqual(installer.select_asset(assets, 'arm64')['name'], 'claude-desktop-unofficial_2_arm64.deb')
        with self.assertRaises(RuntimeError):
            installer.select_asset(assets, 'riscv64')


class EngineTests(unittest.TestCase):
    def setUp(self):
        state_directory = tempfile.TemporaryDirectory()
        self.addCleanup(state_directory.cleanup)
        state_patch = patch.object(config, 'STATE', Path(state_directory.name))
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.home_directory = Path(state_directory.name) / 'desktop-home'
        home_patch = patch('webapp.engine.HOME_DIRECTORY', self.home_directory)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        platform_patch = patch('webapp.engine.platform.system', return_value='Linux')
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
        with patch.object(config, 'load', return_value=dict(config.DEFAULTS)):
            self.engine = Engine()

    def test_container_keeps_home_and_only_publishes_local_novnc(self):
        calls = []
        class Result:
            returncode = 0
        def run(args, **kwargs):
            calls.append(args)
            return Result()
        with patch.object(self.engine, 'probe', return_value={'connected': True}), \
             patch.object(self.engine, 'inspect', side_effect=[None, {'State': {'Running': True}}]), \
             patch.object(self.engine, 'desktop', return_value=True), \
             patch.object(self.engine, 'run', side_effect=run):
            self.engine.do_start()
        command = next(c for c in calls if c[0] == 'run')
        self.assertIn('type=bind,source=%s,target=/home/claude' % self.home_directory, command)
        self.assertIn('--userns=keep-id:uid=1000,gid=1000', command)
        self.assertIn('127.0.0.1::6080', command)
        self.assertIn('--cap-drop=all', command)
        password_path = self.home_directory / '.config/claude-podman/keyring-password'
        self.assertEqual(password_path.read_text(), self.engine.config['keyring_password'])
        self.assertEqual(password_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(any(self.engine.config['keyring_password'] in c for c in command))
        self.assertFalse(any('/.claude' in c or '5901:' in c for c in command))

    def test_refuses_foreign_container(self):
        class Result:
            returncode = 0
            stdout = json.dumps([{'Config': {'Labels': {OWNER: 'someone-else'}}}])
        with patch.object(self.engine, 'run', return_value=Result()), self.assertRaises(RuntimeError):
            self.engine.inspect()

    def test_custom_bind_mounts_and_full_hd_resolution(self):
        calls = []
        class Result:
            returncode = 0
        with tempfile.TemporaryDirectory() as source:
            self.engine.config['mounts'] = [{'source': source, 'target': '/workspace', 'readonly': True}]
            self.engine.config['ports'] = [{'host': 18080, 'container': 8080, 'protocol': 'tcp'},
                                           {'host': 18081, 'container': 8081, 'protocol': 'udp'}]
            with patch.object(self.engine, 'probe', return_value={'connected': True}), \
                 patch.object(self.engine, 'inspect', side_effect=[None, {'State': {'Running': True}}]), \
                 patch.object(self.engine, 'desktop', return_value=True), \
                 patch.object(self.engine, 'run', side_effect=lambda args, **kw: (calls.append(args), Result())[1]):
                self.engine.do_start()
            command = next(c for c in calls if c[0] == 'run')
            self.assertIn('type=bind,source=%s,target=/workspace,readonly=true' % source, command)
            self.assertIn('DESKTOP_RESOLUTION=1920x1080', command)
            self.assertIn('type=bind,source=%s,target=/home/claude' % self.home_directory, command)
            self.assertIn('127.0.0.1:18080:8080/tcp', command)
            self.assertIn('127.0.0.1:18081:8081/udp', command)

    def test_migrates_old_home_before_replacing_container(self):
        calls = []
        old = {'State': {'Running': True}, 'Mounts': [{'Destination': '/home/claude', 'Source': '/old-volume'}]}
        class Result:
            returncode = 0
        with patch.object(self.engine, 'probe', return_value={'connected': True}), \
             patch.object(self.engine, 'inspect', side_effect=[old, {'State': {'Running': True}}]), \
             patch.object(self.engine, 'desktop', return_value=True), \
             patch.object(self.engine, 'run', side_effect=lambda args, **kw: (calls.append(args), Result())[1]):
            self.engine.do_start()
        self.assertLess(next(i for i, c in enumerate(calls) if c[0] == 'cp'), next(i for i, c in enumerate(calls) if c[0] == 'rm'))
        self.assertIn(['cp', '--archive=false', NAME + ':/home/claude/.', str(self.home_directory)], calls)

    def test_setup_installs_with_brew_then_prepares_machine_and_proxy(self):
        self.engine.config['proxy'] = 'http://127.0.0.1:10808'
        events = []
        with patch('webapp.engine.platform.system', return_value='Darwin'), \
             patch('webapp.engine.podman_binary', return_value=None), \
             patch('webapp.engine.brew_binary', return_value='/opt/homebrew/bin/brew'), \
             patch.object(self.engine, 'stream_command', side_effect=lambda *a, **kw: events.append(('install', a, kw))), \
             patch.object(self.engine, 'do_machine', side_effect=lambda: events.append(('machine',))), \
             patch.object(self.engine, 'prepare_machine_proxy', side_effect=lambda: events.append(('proxy',))), \
             patch.object(self.engine, 'probe', return_value={'connected': True}):
            self.engine.do_setup()
        self.assertEqual([e[0] for e in events], ['install', 'machine', 'proxy'])
        self.assertEqual(events[0][1][0], ['/opt/homebrew/bin/brew', 'install', 'podman'])
        self.assertEqual(events[0][2]['env']['HTTPS_PROXY'], 'http://127.0.0.1:10808')

    def test_busy_excludes_mutations(self):
        self.engine.busy = True
        with self.assertRaises(ValueError):
            self.engine.submit('start')
        with self.assertRaises(ValueError):
            self.engine.update(dict(config.DEFAULTS))

    def test_failed_rebuild_preserves_existing_container(self):
        with patch.object(self.engine, 'probe', return_value={'connected': True}), \
             patch.object(self.engine, 'inspect', return_value={'State': {'Running': True}}), \
             patch.object(self.engine, 'build', side_effect=RuntimeError('download failed')), \
             patch.object(self.engine, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'download failed'):
                self.engine.do_rebuild()
            run.assert_not_called()

    def test_save_commits_without_volumes_and_uses_snapshot_on_next_start(self):
        with tempfile.TemporaryDirectory() as path, patch.object(config, 'STATE', Path(path)), \
             patch.object(self.engine, 'inspect', return_value={'State': {'Running': True}}), \
             patch.object(self.engine, 'stream') as stream, patch.object(self.engine, 'run') as run:
            self.engine.do_save()
            command = stream.call_args.args[0]
            self.assertEqual(command[:3], ['commit', '--pause=true', NAME])
            self.assertNotIn('--include-volumes', command)
            from webapp.engine import IMAGE
            run.assert_called_once_with(['tag', command[-1], IMAGE])
            saved = json.loads((Path(path) / 'saved-image.json').read_text())
            self.assertEqual(saved['image'], command[-1])
            self.assertEqual(self.engine.saved_image, saved)

    def test_save_requires_existing_owned_container(self):
        with patch.object(self.engine, 'inspect', return_value=None), \
             patch.object(self.engine, 'stream') as stream:
            with self.assertRaises(RuntimeError):
                self.engine.do_save()
            stream.assert_not_called()

    def test_build_passes_proxy_and_version(self):
        self.engine.config.update(proxy='http://127.0.0.1:10808', release='v3.3.17')
        with patch.object(self.engine, 'stream') as stream:
            self.engine.build()
        args = stream.call_args.args[0]
        self.assertIn('https_proxy=http://host.containers.internal:10808', args)
        self.assertIn('CLAUDE_RELEASE=v3.3.17', args)
        self.assertEqual(stream.call_args.kwargs['env']['HTTPS_PROXY'], 'http://127.0.0.1:10808')


class FakeEngine:
    desktop_port = None
    config = dict(config.DEFAULTS)
    def status(self):
        return {'ready': False}
    def update(self, values):
        return config.validate(values)
    def submit(self, action):
        if action not in ('start', 'stop'):
            raise ValueError('未知操作')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.server = Server(('127.0.0.1', 0), self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = 'localhost:%s' % self.server.server_port
        self.cookie = 'desktop_session=' + self.server.session

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, method='GET', data=None, **headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        merged = {'Host': self.host, **headers}
        conn.request(method, path, body=json.dumps(data) if data is not None else None, headers=merged)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_index_bootstraps_session_without_external_assets(self):
        status, headers, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly; SameSite=Strict', headers['Set-Cookie'])
        self.assertIn(self.server.csrf.encode(), body)
        self.assertNotIn(b'__CSRF__', body)

    def test_api_requires_session_and_mutation_requires_csrf(self):
        self.assertEqual(self.request('/api/status')[0], 401)
        self.assertEqual(self.request('/api/status', Cookie=self.cookie)[0], 200)
        self.assertEqual(self.request('/api/config', Cookie=self.cookie)[0], 200)
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'start'}, Cookie=self.cookie)[0], 403)
        headers = {'Cookie': self.cookie, 'X-CSRF-Token': self.server.csrf, 'Origin': 'http://' + self.host}
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'start'}, **headers)[0], 202)
        self.assertEqual(self.request('/api/config', 'POST', {'memory': 1}, **headers)[0], 400)

    def test_page_token_works_when_cookies_are_blocked_or_overwritten(self):
        headers = {'X-CSRF-Token': self.server.csrf, 'Origin': 'http://' + self.host}
        self.assertEqual(self.request('/api/config', **headers)[0], 200)
        self.assertEqual(self.request('/api/status', Cookie='desktop_session=another-port', **headers)[0], 200)
        self.assertEqual(self.request('/api/action', 'POST', {'action': 'start'}, **headers)[0], 202)
        self.assertEqual(self.request('/api/config', **{'X-CSRF-Token': 'old-session'})[0], 401)
        headers['Origin'] = 'http://evil.example'
        self.assertEqual(self.request('/api/config', **headers)[0], 403)

    def test_desktop_token_is_required_without_cookie(self):
        self.assertEqual(self.request('/desktop/wrong-token/vnc.html')[0], 401)
        self.assertEqual(self.request('/desktop/vnc.html')[0], 401)
        # Valid authentication reaches the offline-desktop check.
        self.assertEqual(self.request('/desktop/%s/vnc.html' % self.server.csrf)[0], 503)

    def test_dns_rebinding_and_foreign_origin_rejected(self):
        self.assertEqual(self.request('/', Host='evil.example')[0], 403)
        self.assertEqual(self.request('/api/status', Cookie=self.cookie, Origin='http://evil.example')[0], 403)

    def test_no_arbitrary_static_files(self):
        self.assertEqual(self.request('/../README.md')[0], 404)
        self.assertEqual(self.request('/app.js')[0], 200)

    def test_static_mime_types_ignore_system_file_associations(self):
        with patch('mimetypes.guess_type', return_value=('text/plain', None)):
            for path, expected in [('/app.js', 'text/javascript; charset=utf-8'),
                                   ('/desktop-storage.js', 'text/javascript; charset=utf-8'),
                                   ('/style.css', 'text/css; charset=utf-8')]:
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], expected)
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertTrue(body)

    def test_desktop_html_loads_storage_fallback_before_novnc(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(5)
        self.engine.desktop_port = listener.getsockname()[1]
        html = b'<!DOCTYPE html><html><head><script type="module" src="app/ui.js"></script></head></html>'
        observed = []

        def backend():
            with contextlib.closing(listener):
                remote, _ = listener.accept()
                with remote:
                    remote.settimeout(5)
                    request = b''
                    while b'\r\n\r\n' not in request:
                        request += remote.recv(1)
                    observed.append(request)
                    remote.sendall(('HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %s\r\nETag: old\r\n\r\n' % len(html)).encode() + html)

        thread = threading.Thread(target=backend)
        thread.start()
        status, headers, body = self.request('/desktop/%s/vnc.html?autoconnect=1' % self.server.csrf)
        thread.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(int(headers['Content-Length']), len(body))
        self.assertIn(b'<head><script src="/desktop-storage.js"></script><script type="module"', body)
        self.assertNotIn('ETag', headers)
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertIn(b'GET /vnc.html?autoconnect=1 ', observed[0])
        self.assertIn(b'Accept-Encoding: identity', observed[0])
        self.assertNotIn(self.server.csrf.encode(), observed[0])

    def test_websocket_proxy_preserves_initial_bytes_and_bidirectional_frames(self):
        self.check_websocket_proxy(cookie_free=False)

    def test_websocket_proxy_accepts_page_token_without_cookies(self):
        self.check_websocket_proxy(cookie_free=True)

    def check_websocket_proxy(self, cookie_free):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(5)
        self.engine.desktop_port = listener.getsockname()[1]
        observed = []
        def backend():
            with contextlib.closing(listener):
                remote, _ = listener.accept()
                with remote:
                    remote.settimeout(5)
                    request = b''
                    while b'\r\n\r\n' not in request:
                        request += remote.recv(1)
                    observed.append(request)
                    remote.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n\x82\x03abc')
                    observed.append(remote.recv(100))
                    remote.sendall(b'\x82\x03def')
        backend_thread = threading.Thread(target=backend)
        backend_thread.start()
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=5) as client:
            target = '/desktop/%s/websockify' % self.server.csrf if cookie_free else '/desktop/websockify'
            cookie = '' if cookie_free else 'Cookie: %s\r\n' % self.cookie
            client.sendall(('GET %s HTTP/1.1\r\nHost: %s\r\n%sOrigin: http://%s\r\nReferer: http://%s/desktop/%s/vnc.html\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n' % (target, self.host, cookie, self.host, self.host, self.server.csrf)).encode())
            headers = b''
            while b'\r\n\r\n' not in headers:
                headers += client.recv(1)
            self.assertIn(b'101', headers)
            self.assertEqual(client.recv(5), b'\x82\x03abc')
            client.sendall(b'client-frame')
            self.assertEqual(client.recv(5), b'\x82\x03def')
        backend_thread.join(timeout=5)
        self.assertIn(b'GET /websockify ', observed[0])
        self.assertNotIn(b'Cookie:', observed[0])
        self.assertNotIn(self.server.csrf.encode(), observed[0])
        self.assertNotIn(b'Referer:', observed[0])
        self.assertEqual(observed[1], b'client-frame')


if __name__ == '__main__':
    unittest.main()
