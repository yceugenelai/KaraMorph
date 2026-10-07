"""Lyrics, background images, and approximate vocal activity for karaoke display."""

from app.i18n import t
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal
import sys
import json
import hashlib
from runtime_config import ROOT
from app.vocal_activity import cache_path, read_cache

from app.storage import song_dir

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".svg"}


def lyrics_path(workspace: Path, song_id: str) -> Path:
    return song_dir(workspace, song_id) / "assets" / "lyrics.txt"


def song_images_dir(workspace: Path, song_id: str) -> Path:
    return song_dir(workspace, song_id) / "assets" / "images"


def shared_images_dir(workspace: Path) -> Path:
    folder = workspace / "shared_images"
    retired = folder / "placeholder.svg"
    # Remove only our exact retired image, never a user's same-name replacement.
    if retired.is_file() and not retired.is_symlink():
        if hashlib.sha256(retired.read_bytes()).hexdigest() == "eeb8935803e809f94522a9a63bcddf662cf6b3bec82ffb5e41ee65f7d6a13ffb":
            retired.unlink()
    defaults = image_files(ROOT / "assets" / "backgrounds")
    if not defaults:
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / ".karamorph-default-images.json"
    if marker.exists():
        initialized = set(json.loads(marker.read_text(encoding="utf-8")))
    else:
        # Migrate the previous single-image initializer without restoring a
        # placeholder the user already deleted.
        initialized = {"placeholder.svg"} if (folder / ".karamorph-defaults-v1").exists() else set()
    previous = set(initialized)
    for source in defaults:
        if source.name in initialized:
            continue
        target = folder / source.name
        try:
            with target.open("xb") as output:
                output.write(source.read_bytes())
        except FileExistsError:
            pass  # Preserve user replacements and images with the same name.
        initialized.add(source.name)
    if initialized != previous or not marker.exists():
        pending = marker.with_suffix(".tmp")
        pending.write_text(json.dumps(sorted(initialized), ensure_ascii=False), encoding="utf-8")
        pending.replace(marker)
    return folder


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
                  key=lambda p: p.name.casefold())


def read_lyrics(workspace: Path, song_id: str) -> str:
    path = lyrics_path(workspace, song_id)
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def save_lyrics(workspace: Path, song_id: str, value: str) -> None:
    path = lyrics_path(workspace, song_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class ActivityTask(QObject):
    ready = Signal(str, object, object)
    failed = Signal(str)

    def __init__(self, path: Path, folder: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.target = cache_path(path, folder)
        self.signals = self
        self.done = False
        self.error_text = b""
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(ROOT))
        from PySide6.QtCore import QProcessEnvironment
        env = QProcessEnvironment.systemEnvironment()
        for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
            env.insert(name, '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self):
        cached = read_cache(self.path, self.target)
        if cached is not None:
            self.done = True
            self.ready.emit(str(self.path), *cached)
            self.deleteLater()
            return
        args = ['-m', 'app.vocal_activity', '--input', str(self.path),
                '--cache', str(self.target)]
        if getattr(sys, 'frozen', False):
            args = ['--vocal-activity', '--input', str(self.path), '--cache', str(self.target)]
        self.process.start(sys.executable, args)

    def _stderr(self):
        self.error_text = (self.error_text + bytes(self.process.readAllStandardError()))[-12000:]

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._fail(self.process.errorString())

    def _fail(self, message):
        if not self.done:
            self.done = True
            self.failed.emit(message)
            self.deleteLater()

    def _finished(self, code, status):
        if self.done:
            return
        self._stderr()
        cached = read_cache(self.path, self.target) if code == 0 else None
        if cached is None:
            self._fail(self.error_text.decode('utf-8', errors='replace') or t('人聲分析失敗或快取無效'))
        else:
            self.done = True
            self.ready.emit(str(self.path), *cached)
            self.deleteLater()

    def cancel(self):
        self.done = True
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(1000)
        self.deleteLater()
