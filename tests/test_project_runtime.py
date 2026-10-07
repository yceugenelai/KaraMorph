import os
import tempfile
import unittest
import types
import shutil
from pathlib import Path
from unittest.mock import patch

import runtime_config as cfg


class ProjectRuntimeTests(unittest.TestCase):
    def test_defaults_and_caches_are_project_owned(self):
        env = cfg.worker_env()
        for key, value in env.items():
            if key not in {"PYTHONUTF8", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}:
                self.assertTrue(Path(value).is_relative_to(cfg.ROOT), (key, value))
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")

    def test_invalid_explicit_runtime_does_not_fall_back(self):
        self.assertIsNone(cfg.runtime({"roformer_python": "missing/python.exe"}, "roformer_python"))

    def test_environment_check_does_not_create_selected_asset_directories(self):
        with tempfile.TemporaryDirectory(dir=cfg.ROOT / ".app_data/cache/tmp") as folder:
            model_path = Path(folder) / "missing-models"
            cfg.worker_env({"roformer_model_dir": str(model_path), "acestep_model_dir": str(model_path)})
            self.assertFalse(model_path.exists())

    def test_worker_uses_project_caches_and_keeps_selected_assets(self):
        with tempfile.TemporaryDirectory(dir=cfg.ROOT / ".app_data/cache/tmp") as folder:
            selected = Path(folder) / "models"
            with patch.dict(os.environ, {"HF_HOME": "external-cache", "NUMBA_CACHE_DIR": "external-cache",
                                         "AIK_ROFORMER_MODEL_DIR": str(selected)}, clear=True):
                with patch.object(cfg, "prepare_ffmpeg"):
                    cfg.bootstrap_worker()
                self.assertEqual(Path(os.environ["AIK_ROFORMER_MODEL_DIR"]), selected)
                self.assertTrue(Path(os.environ["HF_HOME"]).is_relative_to(cfg.ROOT))
                self.assertTrue(Path(os.environ["NUMBA_CACHE_DIR"]).is_relative_to(cfg.ROOT))
                self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")

    def test_ffmpeg_is_available_without_system_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'imageio-ffmpeg-version.exe'
            source.write_bytes(b'bundled binary version one')
            imageio = types.SimpleNamespace(get_ffmpeg_exe=lambda: str(source))
            with patch.object(cfg, 'ROOT', root), patch.dict('sys.modules', {'imageio_ffmpeg': imageio}), patch.dict(os.environ, {'PATH': ''}):
                first = cfg.prepare_ffmpeg()
                self.assertEqual(Path(shutil.which('ffmpeg')), first)
                self.assertEqual(first.read_bytes(), source.read_bytes())
                cfg.prepare_ffmpeg()
                self.assertEqual(os.environ['PATH'].split(os.pathsep).count(str(first.parent)), 1)
                source.write_bytes(b'bundled binary version two')
                second = cfg.prepare_ffmpeg()
                self.assertNotEqual(first, second)
                self.assertEqual(first.read_bytes(), b'bundled binary version one')
                self.assertEqual(second.read_bytes(), b'bundled binary version two')

    def test_relative_settings_are_independent_of_working_directory(self):
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                self.assertEqual(cfg.configured_path({"roformer_model_dir": "custom/models"}, "roformer_model_dir"), cfg.ROOT / "custom/models")
            finally:
                os.chdir(before)

    def test_old_poc_settings_are_reported_without_modification(self):
        settings = {"roformer_python": str(cfg.ROOT.parent / "ai_karaoke_tool_test/.conda/python.exe")}
        original = dict(settings)
        self.assertTrue(cfg.migration_issues(settings))
        self.assertEqual(settings, original)


if __name__ == "__main__":
    unittest.main()
