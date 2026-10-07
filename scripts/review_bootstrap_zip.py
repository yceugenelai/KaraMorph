"""Verify the recommended bootstrap ZIP against current distributable source."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from export_source import ROOT, source_paths


def review(archive_path):
    archive_path = Path(archive_path)
    scripts = {'BootstrapPoc.cs', 'bootstrap_models.py', 'bootstrap_smoke.py',
               'requirements-ui-windows.lock.txt', 'requirements-separation-windows.lock.txt',
               'requirements-styling-windows.lock.txt'}
    root_files = {'run_app.py', 'runtime_config.py', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                  'README.md', 'README.zh-TW.md', 'README.ja.md'}
    expected = {}
    for source in source_paths(ROOT):
        relative = source.relative_to(ROOT)
        if relative.parts[0] in {'app', 'workers', 'docs', 'assets'} or (relative.parts[0] == 'scripts' and relative.name in scripts) or relative.as_posix() in root_files:
            expected[relative.as_posix()] = source.read_bytes()
    records = []
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC check failed')
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP entries')
        relative_names = set()
        for name in names:
            parts = Path(name).parts
            if len(parts) < 2 or parts[0] != 'KaraMorph-bootstrap-poc' or '..' in parts or '\\' in name:
                raise ValueError(f'Unexpected ZIP path: {name}')
            relative = '/'.join(parts[1:])
            relative_names.add(relative)
            data = archive.read(name)
            if relative in expected and data != expected[relative]:
                raise ValueError(f'Stale source or notice: {relative}')
            records.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        if relative_names != set(expected) | {'KaraMorph.exe', 'README.txt', 'bootstrap-launcher.json'}:
            raise ValueError(f'Unexpected or missing files: {relative_names ^ (set(expected) | {"KaraMorph.exe", "README.txt", "bootstrap-launcher.json"})}')
        launcher = archive.read('KaraMorph-bootstrap-poc/KaraMorph.exe')
        if launcher != (ROOT / 'build/bootstrap-poc-seed/KaraMorph.exe').read_bytes():
            raise ValueError('Launcher differs from build seed')
    report = {'scope': 'bootstrap-without-bundled-uv', 'artifact_checks_passed': True,
              'legal_certification': False, 'archive_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest(),
              'files': records}
    destination = archive_path.with_suffix('.distribution.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Verified {len(records)} files; inventory: {destination.name}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('zip', type=Path)
    review(parser.parse_args().zip)
