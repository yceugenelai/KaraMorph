"""Create a small first-run experiment, independently of the full portable ZIP."""
import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path
from export_source import source_paths

ROOT = Path(__file__).resolve().parents[1]


def build(with_uv=False, repack=False):
    label = 'KaraMorph-bootstrap-poc' + ('-with-uv' if with_uv else '')
    target = ROOT / 'dist' / label
    if repack:
        if not (target / 'KaraMorph.exe').is_file():
            raise ValueError('No existing prototype to repack')
        refresh(target)
        package(target, with_uv)
        return
    if target.exists():
        raise ValueError(f'Existing experiment is preserved: {target}')
    target.mkdir(parents=True)
    for directory in ('app', 'workers', 'docs', 'assets'):
        shutil.copytree(ROOT / directory, target / directory, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('run_app.py', 'runtime_config.py', 'LICENSE', 'README.md', 'README.zh-TW.md', 'README.ja.md', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, target / name)
    scripts = target / 'scripts'
    scripts.mkdir()
    for name in ('BootstrapPoc.cs', 'bootstrap_models.py', 'bootstrap_smoke.py', 'requirements-ui-windows.lock.txt',
                 'requirements-separation-windows.lock.txt', 'requirements-styling-windows.lock.txt'):
        shutil.copy2(ROOT / 'scripts' / name, scripts / name)
    shutil.copy2(ROOT / 'build/bootstrap-poc-seed/KaraMorph.exe', target / 'KaraMorph.exe')
    if with_uv:
        seed = ROOT / 'build/bootstrap-poc-seed/uv.exe'
        destination = target / '.tools/bin/uv.exe'
        destination.parent.mkdir(parents=True)
        shutil.copy2(seed, destination)
        destination.with_suffix('.exe.sha256').write_text(hashlib.sha256(seed.read_bytes()).hexdigest())
    refresh(target)
    package(target, with_uv)


def refresh(target):
    for directory in ('app', 'workers', 'docs', 'assets'):
        shutil.copytree(ROOT / directory, target / directory, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('run_app.py', 'runtime_config.py', 'LICENSE', 'README.md', 'README.zh-TW.md', 'README.ja.md', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, target / name)
    for name in ('BootstrapPoc.cs', 'bootstrap_models.py', 'bootstrap_smoke.py', 'requirements-ui-windows.lock.txt', 'requirements-separation-windows.lock.txt', 'requirements-styling-windows.lock.txt'):
        shutil.copy2(ROOT / 'scripts' / name, target / 'scripts' / name)
    shutil.copy2(ROOT / 'build/bootstrap-poc-seed/KaraMorph.exe', target / 'KaraMorph.exe')
    (target / 'bootstrap-launcher.json').write_text('{"version": 2}\n', encoding='utf-8')
    (target / 'README.txt').write_text('KaraMorph first-run setup\n\nExtract into a writable folder and run KaraMorph.exe. Choose English, Traditional Chinese or Japanese. Vocal separation and ACE-Step runtimes AND models are selected by default. Key/speed changes require ACE-Step runtime. Preparation may need 35-40 GiB including caches. Internet is required initially.\n\nCancel preserves completed components; retry failed components or start with the basic UI. Later launches use the saved language without installing unchecked features. Settings > Manage installation opens setup again. Logs: .app_data/logs/bootstrap.log. No models are included in this ZIP. See docs/DISTRIBUTION.md for the bootstrap ZIP distribution scope and notices.\n', encoding='utf-8')


def package(target, with_uv):
    archive = target.with_suffix('.zip')
    # Collect from the current source list, not an old staging tree. This
    # prevents removed/archived files from returning in a repacked archive.
    scripts = {'BootstrapPoc.cs', 'bootstrap_models.py', 'bootstrap_smoke.py', 'requirements-ui-windows.lock.txt',
               'requirements-separation-windows.lock.txt', 'requirements-styling-windows.lock.txt'}
    files = []
    for source in source_paths(ROOT):
        relative = source.relative_to(ROOT)
        if relative.parts[0] in {'app', 'workers', 'docs', 'assets'} or (relative.parts[0] == 'scripts' and relative.name in scripts):
            files.append(target / relative)
    files += [target / n for n in ('KaraMorph.exe', 'run_app.py', 'runtime_config.py', 'LICENSE', 'README.md', 'README.zh-TW.md', 'README.ja.md', 'THIRD_PARTY_NOTICES.md', 'README.txt', 'bootstrap-launcher.json')]
    if with_uv:
        files += [target / '.tools/bin/uv.exe', target / '.tools/bin/uv.exe.sha256']
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipout:
        for path in sorted(files):
            zipout.write(path, path.relative_to(target.parent).as_posix())
    archive.with_suffix('.zip.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + archive.name + '\n')
    print(f'{archive.name}: {archive.stat().st_size} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--with-uv', action='store_true')
    parser.add_argument('--repack', action='store_true')
    args = parser.parse_args()
    build(args.with_uv, args.repack)
