"""Prepare the optional guitar runtime without altering existing environments."""
import os
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runtime_config import worker_env

def run(args, env):
    print(' '.join(str(x) for x in args), flush=True)
    subprocess.run([str(x) for x in args], cwd=ROOT, env=env, check=True)

def main():
    env = os.environ.copy()
    env.update(worker_env())
    env.pop('PIP_REQUIRE_VIRTUALENV', None)
    folder = ROOT / '.runtime' / 'guitar'
    marker = folder / 'karamorph-guitar.ready'
    marker.unlink(missing_ok=True)
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        run([sys.executable, '-m', 'venv', str(folder)], env)
    run([python, '-m', 'pip', 'install', '--upgrade', 'pip==26.3'], env)
    run([python, '-m', 'pip', 'install', '--index-url', 'https://download.pytorch.org/whl/cpu', 'torch==2.7.1'], env)
    download = ROOT/'.app_data/cache/guitar-install'
    download.mkdir(parents=True,exist_ok=True)
    wheel = download/'lv_chordia-1.1.0-py3-none-any.whl'
    run([python, '-m', 'pip', 'download', '--no-deps', '--dest', download, 'lv-chordia==1.1.0'], env)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if digest != '292241f03e1029e6739ee7a2a91fbe348c25c97b8b3641db76cbef9f9dee0cb0':
        raise ValueError('LV-Chordia wheel hash mismatch; installation stopped')
    run([python, '-m', 'pip', 'install', '--no-deps', '--force-reinstall', wheel], env)
    run([python, '-m', 'pip', 'install', '-r', ROOT/'app/plugins/guitar/requirements-windows.lock.txt'], env)
    run([python, '-c', "import lv_chordia,librosa,torch; print('guitar runtime ready')"], env)
    marker.write_text('lv-chordia==1.1.0\n', encoding='utf-8')

if __name__ == '__main__':
    main()
