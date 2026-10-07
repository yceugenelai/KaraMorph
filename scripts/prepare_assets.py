"""Developer asset preparation; GUI users consent through the model manager."""
import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.model_store import ModelStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ace-assets', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--source-only', action='store_true')
    parser.add_argument('--groups', nargs='+', choices=['separation', 'styling'], default=['separation', 'styling'])
    args = parser.parse_args()
    store = ModelStore()
    if args.source_only:
        if args.check:
            if not store.source_ready():
                raise RuntimeError('Pinned ACE-Step source is missing')
        else:
            store.prepare_source(threading.Event(), lambda done, total: print(f'ACE source {done}/{total}', flush=True))
        return
    if 'styling' in args.groups and not args.check:
        store.prepare_source(threading.Event(), lambda done, total: print(f'ACE source {done}/{total}', flush=True))
    store.prepare(args.groups, import_root=args.ace_assets, verify_only=args.check,
                  emit=lambda event: print(event, flush=True))


if __name__ == '__main__':
    main()
