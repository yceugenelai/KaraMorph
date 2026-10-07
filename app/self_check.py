"""Portable acceptance without network, music or model downloads."""
import os
import subprocess
import tempfile
from pathlib import Path


def check(root):
    root = Path(root).resolve()
    from PySide6.QtWidgets import QApplication, QMessageBox
    from app.consumer import ConsumerWindow
    from app.i18n import configure
    from runtime_config import ROOT, DEFAULTS, worker_env
    assert ROOT == root
    app = QApplication.instance() or QApplication([])
    for code in ('zh_TW', 'en', 'ja'):
        configure(code)
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        assert box.button(QMessageBox.StandardButton.Ok).text()
    window = ConsumerWindow()
    assert window.windowTitle() == 'KaraMorph'
    window.open_models()
    assert window.models_dialog.worker is None
    window.models_dialog.close()
    window.close()
    for group, key in [('separation', 'roformer_python'), ('styling', 'acestep_python')]:
        python = DEFAULTS[key]
        assert python.is_relative_to(root) and python.is_file(), python
        code = 'import sys,torch,soundfile,imageio_ffmpeg; print(sys.prefix); print(torch.cuda.is_available())'
        result = subprocess.run([str(python), '-c', code], cwd=root, env={**os.environ, **worker_env()},
                                capture_output=True, encoding='utf-8', timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr)
        assert str(root / '.runtime' / group).lower() in result.stdout.lower(), result.stdout
        print(group + ': relocated interpreter and audio/torch imports okay', flush=True)
    import tinytag
    import numpy as np
    import soundfile as sf
    with tempfile.TemporaryDirectory(dir=root / '.app_data') as folder:
        folder = Path(folder)
        source = folder / 'synthetic.wav'
        sf.write(source, np.sin(np.arange(48000 * 2) * (2 * np.pi * 440 / 48000)) * 0.1, 48000)
        commands = [
            [str(DEFAULTS['acestep_python']), str(root / 'workers/fx_worker.py'), '--backing', str(source),
             '--output-dir', str(folder / 'fx'), '--speed', '0.9', '--semitones', '2'],
            [str(root / '.runtime/ui/python.exe'), str(root / 'run_app.py'), '--vocal-activity',
             '--input', str(source), '--cache', str(folder / 'vocal.json')],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=root, env={**os.environ, **worker_env()},
                                    capture_output=True, encoding='utf-8', timeout=120)
            if result.returncode:
                raise RuntimeError(result.stderr + result.stdout)
        assert abs(sf.info(folder / 'fx/backing.wav').duration - 2 / 0.9) < 0.1
        assert (folder / 'vocal.json').is_file()
        print('Synthetic key/speed and vocal-analysis workers passed.', flush=True)
    print('KaraMorph portable smoke passed; models and physical devices not exercised.', flush=True)
