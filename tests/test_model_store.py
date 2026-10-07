import hashlib
import http.server
import tempfile
import threading
import unittest
from pathlib import Path

from app.model_store import Cancelled, ModelStore


class ModelStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b'KaraMorph test asset' * 100
        self.asset = dict(path='models/separation/test.bin', size=len(self.payload),
                          sha256=hashlib.sha256(self.payload).hexdigest())
        self.store = ModelStore(root=self.root, manifest=dict(files=[self.asset], ace_source_revision='test'))

    def test_verified_import_and_receipt(self):
        seed = self.root / 'seed'
        seed.mkdir()
        (seed / 'test.bin').write_bytes(self.payload)
        self.store.prepare(['separation'], import_root=seed)
        self.assertTrue(self.store.ready('separation'))
        self.assertTrue(ModelStore(root=self.root, manifest=self.store.manifest).ready('separation'))
        self.store.target(self.asset).write_bytes(b'corrupted')
        self.assertFalse(self.store.ready('separation'))
        with self.assertRaises(ValueError):
            self.store.prepare(['separation'], verify_only=True)

    def test_bad_download_preserves_existing_file(self):
        target = self.store.target(self.asset)
        target.parent.mkdir(parents=True)
        target.write_bytes(b'old healthy version')
        partial = target.with_suffix('.part')
        partial.write_bytes(b'bad')
        with self.assertRaises(ValueError):
            self.store.promote(partial, target, self.asset, threading.Event())
        self.assertEqual(target.read_bytes(), b'old healthy version')
        self.assertFalse(partial.exists())

    def test_cancel_and_unsafe_paths(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            self.store.prepare(['separation'], cancelled)
        with self.assertRaises(ValueError):
            self.store.target(dict(path='models/../escape'))

    def test_resume_when_server_ignores_range(self):
        payload = self.payload
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.asset['url'] = f'http://127.0.0.1:{server.server_port}/asset'
        target = self.store.target(self.asset)
        target.parent.mkdir(parents=True)
        target.with_name(target.name + '.part').write_bytes(payload[:40])
        self.store.fetch(self.asset, target, threading.Event(), lambda *args: None)
        self.assertEqual(target.read_bytes(), payload)
