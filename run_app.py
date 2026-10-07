"""KaraMorph entry point; portable startup errors are logged and shown."""
import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    if '--vocal-activity' in sys.argv:
        sys.argv.remove('--vocal-activity')
        import runpy
        runpy.run_module('app.vocal_activity', run_name='__main__')
        return 0
    if '--self-test' in sys.argv:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from app.self_check import check
        check(root)
        return 0
    logs = root / '.app_data/logs'
    logs.mkdir(parents=True, exist_ok=True)
    log = (logs / 'ui.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    from app.consumer import main as consumer
    return consumer()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            target = Path(__file__).resolve().parent / '.app_data/logs/startup-error.log'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(detail, encoding='utf-8')
            message = 'KaraMorph could not start. Details: ' + str(target)
        except OSError:
            message = 'KaraMorph needs a writable folder. Extract to another folder.\n' + detail[-1000:]
        if os.name == 'nt' and '--self-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'KaraMorph', 0x10)
        else:
            print(detail, file=sys.stderr)
        raise SystemExit(1)
