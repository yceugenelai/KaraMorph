"""Model preparation bridge for the native bootstrap; uses the pinned manifest."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model_store import ModelStore
from app.storage import load_settings


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check', 'prepare'))
    parser.add_argument('--group', choices=('separation', 'styling'), required=True)
    args = parser.parse_args(argv)
    store = ModelStore(load_settings())
    if args.action == 'check':
        return 0 if store.ready(args.group) else 2
    store.prepare([args.group], emit=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    return 0 if store.ready(args.group) else 2


if __name__ == '__main__':
    sys.exit(main())
