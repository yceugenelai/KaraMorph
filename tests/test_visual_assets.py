import tempfile
import unittest
import struct
from unittest.mock import patch
from scripts.build_icon import build_icon
from pathlib import Path
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication
from app.karaoke_assets import shared_images_dir, image_files
from runtime_config import ROOT


class VisualAssetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_is_renderable_and_deleted_default_stays_deleted(self):
        self.assertFalse(QIcon(str(ROOT / 'assets/icon.svg')).isNull())
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            folder = shared_images_dir(workspace)
            self.assertEqual(folder, workspace / 'shared_images')
            default = folder / image_files(ROOT / 'assets/backgrounds')[0].name
            self.assertIn(default, image_files(folder))
            self.assertFalse(QPixmap(str(default)).isNull())
            default.unlink()
            shared_images_dir(workspace)
            self.assertNotIn(default, image_files(folder))

    def test_existing_backgrounds_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            folder = workspace / 'shared_images'
            folder.mkdir()
            image = folder / 'custom.png'
            image.write_bytes(b'user image')
            shared_images_dir(workspace)
            self.assertIn(image, image_files(folder))
            for default in image_files(ROOT / "assets/backgrounds"):
                self.assertIn(folder / default.name, image_files(folder))
            self.assertEqual(image.read_bytes(), b'user image')

    def test_all_defaults_new_files_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'package'
            defaults = root / 'assets/backgrounds'
            defaults.mkdir(parents=True)
            for name in ('placeholder.svg', 'one.PNG', 'two.jpg', 'three.bmp', 'ignore.txt'):
                (defaults / name).write_bytes(b'packaged image')
            workspace = Path(temporary) / 'outputs'
            folder = workspace / 'shared_images'
            folder.mkdir(parents=True)
            (folder / '.karamorph-defaults-v1').touch()
            (folder / 'one.PNG').write_bytes(b'user replacement')
            with patch('app.karaoke_assets.ROOT', root):
                shared_images_dir(workspace)
                self.assertFalse((folder / 'placeholder.svg').exists())
                self.assertFalse((folder / 'ignore.txt').exists())
                self.assertEqual((folder / 'one.PNG').read_bytes(), b'user replacement')
                self.assertTrue((folder / 'three.bmp').exists())
                (folder / 'two.jpg').unlink()
                (defaults / 'new.webp').write_bytes(b'new release')
                shared_images_dir(workspace)
                self.assertFalse((folder / 'two.jpg').exists())
                self.assertEqual((folder / 'new.webp').read_bytes(), b'new release')

    def test_executable_svg_generates_multisize_ico(self):
        with tempfile.TemporaryDirectory() as temporary:
            icon = build_icon(ROOT / 'assets/icon_ico.svg', Path(temporary) / 'icon.ico')
            data = icon.read_bytes()
            self.assertEqual(struct.unpack_from('<HHH', data), (0, 1, 7))
            for i, size in enumerate((16, 24, 32, 48, 64, 128, 256)):
                width, height, _, _, planes, bits, length, offset = struct.unpack_from('<BBBBHHII', data, 6 + i * 16)
                self.assertEqual((width or 256, height or 256, planes, bits), (size, size, 1, 32))
                self.assertEqual(data[offset:offset+8], b'\x89PNG\r\n\x1a\n')
                self.assertLessEqual(offset + length, len(data))
            self.assertFalse(QPixmap(str(icon)).isNull())

    def test_retired_default_cleanup_preserves_user_same_name_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            folder = workspace / 'shared_images'
            folder.mkdir()
            retired = folder / 'placeholder.svg'
            retired.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080"><defs><linearGradient id="sky" x2="1" y2="1"><stop stop-color="#15263f"/><stop offset="1" stop-color="#486b81"/></linearGradient></defs><rect width="1920" height="1080" fill="url(#sky)"/><circle cx="1450" cy="250" r="150" fill="#87c9c7" opacity=".18"/><path d="M0 800Q400 400 960 800T1920 650V1080H0Z" fill="#64ded2" opacity=".13"/><path d="M0 920Q700 620 1200 900T1920 790V1080H0Z" fill="#fff" opacity=".06"/><text x="96" y="140" font-family="sans-serif" font-size="48" fill="#fff" opacity=".65">KaraMorph</text></svg>')
            shared_images_dir(workspace)
            self.assertFalse(retired.exists())
            retired.write_bytes(b'user replacement')
            shared_images_dir(workspace)
            self.assertEqual(retired.read_bytes(), b'user replacement')
