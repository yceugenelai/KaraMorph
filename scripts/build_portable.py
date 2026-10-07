"""Build a relocatable onedir tree from verified, pinned Python environments.

The actual interpreters are flattened into standalone installations: no venv,
pyvenv.cfg, base-interpreter path, registry lookup or seed path is retained.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0-preview'


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def describe(python):
    code = '''import sys,json,importlib.metadata as m
print(json.dumps({'base':sys.base_prefix,'version':list(sys.version_info[:3]),'distributions':[
{'name':d.metadata['Name'],'version':d.version,'root':str(d.locate_file('')),
 'license':d.metadata.get('License-Expression') or d.metadata.get('License',''),
 'classifiers':d.metadata.get_all('Classifier',[]),
 'urls':d.metadata.get_all('Project-URL',[]),
 'files':[str(f) for f in (d.files or [])]} for d in m.distributions()]}))
'''
    return json.loads(subprocess.check_output([str(python), '-c', code], text=True, encoding='utf-8'))


def package_runtime(python, group, output):
    data = describe(python)
    expected_version = [3, 11, 16] if group == 'styling' else [3, 10, 21]
    if data['version'] != expected_version:
        raise ValueError(f'{group}: expected Python {expected_version}; got {data["version"]}')
    locks = ROOT / 'scripts' / f'requirements-{group}-windows.lock.txt'
    expected = {normalize(a): b for a, b in (line.split('==', 1) for line in locks.read_text().splitlines()
                                             if line.strip() and not line.startswith('#'))}
    installed = {normalize(d['name']): d for d in data['distributions']}
    for name, version in expected.items():
        if name not in installed or installed[name]['version'] != version:
            raise ValueError(f'{group}: pinned dependency missing or different: {name}=={version}')
    base = Path(data['base']).resolve()
    destination = output / '.runtime' / group
    shutil.copytree(base, destination, ignore=shutil.ignore_patterns(
        'site-packages', '__pycache__', '*.pyc', 'test', 'tests', 'include', 'libs', 'Scripts', 'tcl'))
    site = destination / 'Lib/site-packages'
    site.mkdir(parents=True, exist_ok=True)
    records = []
    for name in sorted(expected):
        distribution = installed[name]
        source_root = Path(distribution['root']).resolve()
        for filename in distribution['files']:
            relative = Path(filename)
            if relative.is_absolute() or '..' in relative.parts or '__pycache__' in relative.parts or relative.suffix == '.pyc':
                continue
            source = source_root / relative
            if source.is_file():
                target = site / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                # Repeated files between packages are harmless; everything stays in staging.
                shutil.copy2(source, target)
        record = {key: distribution[key] for key in ('name', 'version', 'license', 'classifiers', 'urls')}
        record['group'] = group
        records.append(record)
    # The Windows base may carry an installer-created absolute sys.path file.
    for pth in site.glob('*.pth'):
        for line in pth.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.strip() and not line.startswith(('#', 'import ')) and Path(line).is_absolute():
                raise ValueError(f'Nonportable .pth entry: {pth.name}')
    if (destination / 'pyvenv.cfg').exists():
        raise ValueError('A virtual environment was copied, not a standalone base')
    print(f'{group}: {len(records)} pinned distributions prepared', flush=True)
    return records


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def inventory(output, records):
    folder = output / 'docs'
    folder.mkdir(exist_ok=True)
    (folder / 'dependency-inventory.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# Actual packaged dependency inventory', '',
             'Generated from the pinned distributions copied into this build. Full wheel notices remain in runtime dist-info folders.', '',
             '| Runtime | Package | Version | Declared license |', '| --- | --- | --- | --- |']
    for record in records:
        label = record['license'] or '; '.join(c.split(' :: ')[-1] for c in record['classifiers'] if c.startswith('License ::')) or 'UNDECLARED — REVIEW'
        label = label.replace('|', '/').replace('\n', ' ')[:240]
        lines.append(f"| {record['group']} | {record['name']} | {record['version']} | {label} |")
    (folder / 'dependency-inventory.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    native = []
    for path in output.rglob('*'):
        if path.suffix.lower() in ('.dll', '.pyd', '.exe'):
            native.append({'path':path.relative_to(output).as_posix(), 'sha256':digest(path)})
    (folder / 'native-inventory.json').write_text(json.dumps(native, indent=2) + '\n', encoding='utf-8')


def finalize(output):
    if not (output / 'KaraMorph.exe').is_file():
        raise ValueError('Launcher has not been compiled')
    excluded = {'.app_data', 'outputs', 'models', 'input', '__pycache__'}
    files = [p for p in output.rglob('*') if p.is_file() and p.suffix != '.pyc' and not excluded.intersection(p.relative_to(output).parts)]
    manifest = [{'path':p.relative_to(output).as_posix(), 'size':p.stat().st_size, 'sha256':digest(p)} for p in files
                if p.name != 'build-manifest.json']
    (output / 'build-manifest.json').write_text(json.dumps({'version':VERSION, 'files':manifest}, indent=2) + '\n')
    archive = output.parent / (output.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as zipped:
        for path in output.rglob('*'):
            if path.is_file() and not excluded.intersection(path.relative_to(output).parts) and path.suffix != '.pyc':
                zipped.write(path, path.relative_to(output.parent).as_posix())
    archive.with_suffix('.zip.sha256').write_text(digest(archive) + '  ' + archive.name + '\n')
    print(f'ZIP ready: {archive.name} ({archive.stat().st_size / 2**30:.2f} GiB)', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ui-python', type=Path, default=ROOT / '.venv/Scripts/python.exe')
    parser.add_argument('--separation-python', type=Path, default=ROOT / '.runtime/separation/Scripts/python.exe')
    parser.add_argument('--styling-python', type=Path, default=ROOT / '.runtime/styling/Scripts/python.exe')
    parser.add_argument('--ace-source', type=Path, default=ROOT / 'third_party/ACE-Step-1.5')
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'dist' / f'KaraMorph-{VERSION}'
    if args.finalize:
        finalize(output)
        return
    if output.exists():
        raise ValueError(f'Staging already exists; choose a new build or remove this exact staging directory: {output}')
    output.mkdir(parents=True)
    for directory in ('app', 'workers', 'docs', 'assets'):
        shutil.copytree(ROOT / directory, output / directory, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('run_app.py', 'runtime_config.py', 'LICENSE', 'README.md', 'README.zh-TW.md', 'README.ja.md', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, output / name)
    revision = json.loads((ROOT / 'docs/model-assets.lock.json').read_text())['ace_source_revision']
    source = args.ace_source.resolve()
    if not (source / '.aik-revision').is_file() or (source / '.aik-revision').read_text().strip() != revision:
        raise ValueError('ACE-Step source revision is missing or not pinned')
    shutil.copytree(source, output / 'third_party/ACE-Step-1.5', ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc', 'checkpoints', '.venv'))
    records = []
    for group, python in [('ui', args.ui_python), ('separation', args.separation_python), ('styling', args.styling_python)]:
        records += package_runtime(python.resolve(), group, output)
    inventory(output, records)
    print('Standalone runtimes ready; compile launcher and run acceptance before finalizing.', flush=True)


if __name__ == '__main__':
    main()
