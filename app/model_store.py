"""Pinned assets, cancellable downloads, validated imports and atomic promotion."""
import hashlib
import json
import shutil
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from runtime_config import ROOT, configured_path


class Cancelled(Exception):
    pass


def sha256(path, cancel=None):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            result.update(chunk)
    return result.hexdigest()


class ModelStore:
    def __init__(self, settings=None, root=None, manifest=None):
        self.root = Path(root or ROOT).resolve()
        self.settings = settings or {}
        self.manifest = manifest or json.loads((self.root / 'docs/model-assets.lock.json').read_text(encoding='utf-8'))
        self.receipt_path = self.root / '.app_data/model-receipts.json'
        try:
            self.receipts = json.loads(self.receipt_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.receipts = {}

    def assets(self, groups):
        prefixes = {'separation': 'models/separation/', 'styling': 'models/acestep/'}
        return [a for a in self.manifest['files'] if any(a['path'].startswith(prefixes[g]) for g in groups)]

    def target(self, asset):
        relative = Path(asset['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'models':
            raise ValueError('Unsafe asset path')
        group = relative.parts[1]
        key = 'roformer_model_dir' if group == 'separation' else 'acestep_model_dir'
        if self.root == ROOT:
            base = configured_path(self.settings, key)
        else:
            base = self.root / 'models' / group
        return base.joinpath(*relative.parts[2:])

    def valid(self, asset):
        path = self.target(asset)
        if not path.is_file():
            return False
        stat = path.stat()
        return self.receipts.get(str(path)) == [asset['sha256'], stat.st_size, stat.st_mtime_ns]

    def remember(self, asset):
        path = self.target(asset)
        stat = path.stat()
        self.receipts[str(path)] = [asset['sha256'], stat.st_size, stat.st_mtime_ns]
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.receipt_path.with_suffix('.tmp')
        partial.write_text(json.dumps(self.receipts, indent=2), encoding='utf-8')
        partial.replace(self.receipt_path)

    def source_root(self):
        return configured_path(self.settings, 'ace_step_root') if self.root == ROOT else self.root / 'third_party/ACE-Step-1.5'

    def source_ready(self):
        folder = self.source_root()
        stamp = folder / '.aik-revision'
        return (stamp.is_file() and stamp.read_text().strip() == self.manifest['ace_source_revision']
                and (folder / 'acestep/handler.py').is_file())

    def ready(self, group):
        return all(self.valid(a) for a in self.assets([group])) and (group != 'styling' or self.source_ready())

    def download_bytes(self, groups):
        total = sum(a.get('size', 0) for a in self.assets(groups) if not self.valid(a))
        if 'styling' in groups and not self.source_ready():
            total += self.manifest.get('source_archive', {}).get('size', 0)
        return total

    def disk_requirements(self, groups):
        # Keep separate budgets for custom model disks and the source/cache disk.
        budgets = {}
        for asset in self.assets(groups):
            if not self.valid(asset):
                parent = self.target(asset).parent
                while not parent.exists():
                    parent = parent.parent
                device = parent.stat().st_dev
                budgets.setdefault(device, [parent, 0])[1] += asset.get('size', 0) * 2
        if 'styling' in groups and not self.source_ready():
            parent = self.root
            budgets.setdefault(parent.stat().st_dev, [parent, 0])[1] += self.manifest['source_archive']['size'] * 8
        for parent, required in budgets.values():
            if shutil.disk_usage(parent).free < required + 256 * 1024 * 1024:
                raise OSError(f'Insufficient free space: {parent}; need approximately {required / 2**30:.1f} GiB')

    def promote(self, partial, target, asset, cancel):
        actual_hash = sha256(partial, cancel)
        actual_size = partial.stat().st_size
        if actual_hash != asset['sha256']:
            # A corrupted prefix must not be resumed on the next attempt.
            partial.unlink(missing_ok=True)
            raise ValueError(f"SHA256 mismatch: {asset['path']}; expected {asset['sha256']} ({asset.get('size', '?')} bytes), received {actual_hash} ({actual_size} bytes)")
        if cancel.is_set():
            raise Cancelled()
        partial.replace(target)

    def fetch(self, asset, target, cancel, progress):
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.part')
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > asset.get('size', 0) > 0:
            partial.unlink()
            offset = 0
        if offset == asset.get('size') and offset:
            self.promote(partial, target, asset, cancel)
            return
        headers = {'User-Agent': 'KaraMorph/0.1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        # URLs are version-pinned, and the complete resulting file is hash checked.
        # No untrusted URL from a server response is executed as code.
        request = urllib.request.Request(asset['url'], headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            if offset and response.status == 206:
                if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                    raise ValueError('Invalid download range response')
                mode = 'ab'
            else:
                offset, mode = 0, 'wb'
            with partial.open(mode) as stream:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    offset += len(chunk)
                    progress(offset, asset.get('size', 0))
        self.promote(partial, target, asset, cancel)

    def prepare_source(self, cancel, progress):
        if self.source_ready():
            return
        source = self.source_root()
        if source.exists() and any(source.iterdir()):
            raise ValueError('ACE-Step source is unverified; choose an empty source folder or repair it using setup.')
        asset = dict(self.manifest['source_archive'], path='ACE-Step source')
        archive = self.root / '.app_data/cache/ace-source.tar.gz'
        self.fetch(asset, archive, cancel, progress)
        staging = self.root / '.app_data/cache/ace-extract'
        if staging.is_symlink() or not staging.resolve().is_relative_to(self.root):
            raise ValueError('Unsafe extraction staging directory')
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            for member in members:
                if cancel.is_set():
                    raise Cancelled()
                target = (staging / member.name).resolve()
                if not target.is_relative_to(staging.resolve()) or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe archive member')
            tar.extractall(staging, members=members, filter='data')
        extracted = staging / f"ACE-Step-1.5-{self.manifest['ace_source_revision']}"
        if not (extracted / 'acestep/handler.py').is_file():
            raise ValueError('ACE-Step archive is missing required source')
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            source.rmdir()
        extracted.replace(source)
        (source / '.aik-revision').write_text(self.manifest['ace_source_revision'] + '\n')
        shutil.rmtree(staging)

    def prepare(self, groups, cancel=None, emit=None, import_root=None, verify_only=False):
        cancel = cancel or threading.Event()
        emit = emit or (lambda event: None)
        assets = self.assets(groups)
        if not verify_only:
            self.disk_requirements(groups)
        for index, asset in enumerate(assets):
            if cancel.is_set():
                raise Cancelled()
            target = self.target(asset)
            emit({'stage': 'checking', 'file': asset['path'], 'index': index, 'count': len(assets)})
            if target.is_file() and sha256(target, cancel) == asset['sha256']:
                self.remember(asset)
                continue
            if verify_only:
                self.receipts.pop(str(target), None)
                self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
                self.receipt_path.write_text(json.dumps(self.receipts, indent=2), encoding='utf-8')
                raise ValueError(f"Missing or invalid asset: {asset['path']}")
            if import_root:
                base = Path(import_root)
                relative = Path(asset['path'])
                candidates = [base / relative, base.joinpath(*relative.parts[1:]),
                              base.joinpath(*relative.parts[2:]), base / asset.get('seed_path', '')]
                source = next((p for p in candidates if p.is_file() and sha256(p, cancel) == asset['sha256']), None)
                if source is None:
                    raise ValueError(f"No matching import file: {asset['path']}")
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + '.part')
                with source.open('rb') as src, partial.open('wb') as dst:
                    for chunk in iter(lambda: src.read(4 * 1024 * 1024), b''):
                        if cancel.is_set():
                            raise Cancelled()
                        dst.write(chunk)
                self.promote(partial, target, asset, cancel)
            else:
                self.fetch(asset, target, cancel, lambda done, total: emit(
                    {'stage': 'downloading', 'file': asset['path'], 'done': done, 'total': total,
                     'index': index, 'count': len(assets)}))
            self.remember(asset)
        if 'styling' in groups and not self.source_ready():
            if verify_only or import_root:
                raise ValueError('Models imported; ACE-Step source must be prepared with Download / repair.')
            self.prepare_source(cancel, lambda done, total: emit(
                {'stage': 'downloading', 'file': 'ACE-Step source', 'done': done, 'total': total,
                 'index': len(assets), 'count': len(assets) + 1}))
