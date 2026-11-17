"""Install upstream's precompiled Fluent assets for GTK and Xfwm4."""
import hashlib
import io
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


REVISION = '7a49a464b0188c340101c52965c18190b1c694cf'
SHA256 = '5d78245fee456dd2d2992c4186a8188b59f87839e7bd5905c56bea00c2446dd4'
URL = 'https://codeload.github.com/vinceliuice/Fluent-gtk-theme/tar.gz/' + REVISION


def main():
    print('Installing Fluent GTK / Xfwm4 theme: ' + REVISION, flush=True)
    with urllib.request.urlopen(URL, timeout=120) as response:
        data = response.read(32 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('Fluent theme SHA256 mismatch')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isdir() or member.isfile()):
                    raise RuntimeError('Invalid theme archive member')
                if member.size > 16 * 1024 * 1024:
                    raise RuntimeError('Theme archive member is too large')
                target = root.joinpath(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
        source = root / ('Fluent-gtk-theme-' + REVISION)
        dest = Path('/usr/share/themes/Fluent-Light')
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / 'COPYING', dest / 'COPYING')
        for version in ('3.0', '4.0'):
            gtk = dest / ('gtk-' + version)
            gtk.mkdir(exist_ok=True)
            shutil.copytree(source / 'src/gtk/assets', gtk / 'assets', dirs_exist_ok=True)
            shutil.copytree(source / 'src/gtk/scalable', gtk / 'assets/scalable', dirs_exist_ok=True)
            for suffix, name in (('Light', 'gtk.css'), ('Dark', 'gtk-dark.css')):
                shutil.copy2(source / ('src/gtk/%s/gtk-%s.css' % (version, suffix)), gtk / name)
        gtk2 = dest / 'gtk-2.0'
        gtk2.mkdir(exist_ok=True)
        shutil.copytree(source / 'src/gtk-2.0/assets-folder/assets', gtk2 / 'assets', dirs_exist_ok=True)
        for name in ('apps.rc', 'hacks.rc', 'main.rc'):
            shutil.copy2(source / 'src/gtk-2.0/common' / name, gtk2 / name)
        shutil.copy2(source / 'src/gtk-2.0/gtkrc', gtk2 / 'gtkrc')
        shutil.copytree(source / 'src/xfwm4/assets-Light', dest / 'xfwm4', dirs_exist_ok=True)
        shutil.copy2(source / 'src/xfwm4/themerc-Light', dest / 'xfwm4/themerc')
        (dest / 'index.theme').write_text('[Desktop Entry]\nType=X-GNOME-Metatheme\nName=Fluent-Light\n'
                                         '[X-GNOME-Metatheme]\nGtkTheme=Fluent-Light\n'
                                         'MetacityTheme=Fluent-Light\nIconTheme=Papirus\n')
        (dest / 'UPSTREAM').write_text('https://github.com/vinceliuice/Fluent-gtk-theme\n' + REVISION + '\n')
    print('Fluent-Light installed with upstream GTK and Xfwm4 assets.', flush=True)


if __name__ == '__main__':
    main()
