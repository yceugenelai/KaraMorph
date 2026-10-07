"""Export only the independent repository source, never local runtime or data."""
import argparse
import shutil
import hashlib
import zipfile
from pathlib import Path

try:
    from .build_portable import VERSION
except ImportError:
    from build_portable import VERSION

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('app', 'workers', 'docs', 'scripts', 'tests', 'assets')
FILES = ('.gitignore', '.gitattributes', 'run_app.py', 'runtime_config.py', 'requirements-ui.txt',
         'LICENSE', 'README.md', 'README.zh-TW.md', 'README.ja.md', 'THIRD_PARTY_NOTICES.md')


def source_paths(root=ROOT):
    root = Path(root).resolve()
    paths = [root / name for name in FILES]
    ignored = {'__pycache__', '.git', '.pytest_cache', '.ruff_cache', '.cache',
               'backup', 'build', 'dist', '.app_data', '.runtime', '.tools',
               'models', 'input', 'outputs', 'third_party'}
    allowed_suffixes = {'.py', '.json', '.md', '.ps1', '.cs', '.txt', '.svg'}
    for directory in DIRECTORIES:
        for path in (root / directory).rglob('*'):
            if not path.is_file() or any(part in ignored for part in path.relative_to(root).parts) or path.suffix == '.pyc':
                continue
            license_text = path.parent == root / 'docs/licenses' and (path.suffix == '' or path.name.endswith('-LICENSE'))
            artwork = (directory == 'assets' or path.is_relative_to(root / 'docs/images')) and path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.ico'}
            if path.suffix not in allowed_suffixes and not license_text and not artwork:
                raise ValueError(f'Unexpected public source file: {path.relative_to(root)}')
            paths.append(path)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f'Unsafe source entry: {path}')
        if path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.pem', '.key', '.p12', '.pfx'}:
            raise ValueError(f'Credential file cannot be exported: {path.relative_to(root)}')
    return sorted(paths)


def export_directory(destination: Path, root=ROOT):
    """Create a fresh repository tree; never overwrite an existing directory."""
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError(f'Existing source directory is preserved: {destination}')
    paths = source_paths(root)
    destination.mkdir(parents=True)
    for source in paths:
        relative = source.relative_to(Path(root).resolve())
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Also create a fresh Git-ready source folder (must not exist)')
    args = parser.parse_args()
    if args.directory:
        export_directory(args.directory)
        print(f'Repository source folder: {args.directory}')
    destination = ROOT / 'dist' / f'KaraMorph-{VERSION}-source.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = source_paths()
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                raise ValueError(f'Unsafe source entry: {path}')
            archive.write(path, 'KaraMorph/' + path.relative_to(ROOT).as_posix())
    destination.with_suffix('.zip.sha256').write_text(
        hashlib.sha256(destination.read_bytes()).hexdigest() + '  ' + destination.name + '\n')
    print(f'Source ZIP: {destination.name}; {len(paths)} files; {destination.stat().st_size} bytes')


if __name__ == '__main__':
    main()
