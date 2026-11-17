"""Resolve the matching community Linux package; never import host credentials."""
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request


def select_asset(assets, arch):
    # Prefer the real unofficial application over the architecture-independent
    # transition package shipped in the same release.
    choices = [a for a in assets if a['name'].endswith('_' + arch + '.deb')]
    choices.sort(key=lambda a: (not a['name'].startswith('claude-desktop-unofficial_'), a['name']))
    if not choices:
        raise RuntimeError('该版本没有 %s 的 Debian 安装包' % arch)
    return choices[0]


def main():
    arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    if arch not in ('amd64', 'arm64'):
        raise RuntimeError('仅支持 amd64 / arm64')
    url = os.environ.get('CLAUDE_DEB_URL', '')
    digest = os.environ.get('CLAUDE_DEB_SHA256', '')
    if not url:
        release = os.environ.get('CLAUDE_RELEASE', 'latest')
        suffix = 'latest' if release == 'latest' else 'tags/' + urllib.parse.quote(release, safe='')
        req = urllib.request.Request('https://api.github.com/repos/aaddrick/claude-desktop-debian/releases/' + suffix,
                                     headers={'User-Agent': 'claude-desktop-podman', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            asset = select_asset(json.load(response)['assets'], arch)
        url = asset['browser_download_url']
        if not digest and asset.get('digest', '').startswith('sha256:'):
            digest = asset['digest'].split(':', 1)[1]
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise RuntimeError('仅允许 HTTPS 安装包')
    print('Installing community Claude Desktop for ' + arch + ': ' + url, flush=True)
    hasher = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=180) as response, open('/tmp/claude-desktop.deb', 'wb') as out:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
            out.write(block)
    if digest and hasher.hexdigest().lower() != digest.lower():
        raise RuntimeError('安装包 SHA256 校验失败')
    print('Package SHA256: ' + hasher.hexdigest(), flush=True)


if __name__ == '__main__':
    main()
