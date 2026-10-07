"""Report every packaged distribution; never infer redistribution clearance."""
import argparse
import json
from pathlib import Path


def review(root):
    records = json.loads((root / 'docs/dependency-inventory.json').read_text(encoding='utf-8'))
    categories = {}
    for entry in records:
        text = (entry['license'] + ' ' + ' '.join(entry['classifiers'])).lower()
        if 'creative commons attribution-noncommercial' in text or 'cc-by-nc' in text:
            category = 'NONCOMMERCIAL restriction / not suitable for an unrestricted commercial release'
        elif entry['name'].lower() in ('numpy', 'scipy'):
            category = 'BSD package with embedded native notices and GCC exceptions / inspect scope'
        elif 'lgpl' in text or 'lesser general' in text:
            category = 'LGPL obligations / inspect bundled notices'
        elif 'gpl' in text or 'general public license' in text:
            category = 'GPL or embedded GPL notice / inspect scope and exceptions'
        elif any(x in text for x in ('mit', 'bsd', 'apache', 'isc', 'python software foundation', 'psf-', 'mpl', 'mozilla', 'public domain', 'unlicense', 'zlib')):
            category = 'Declared license available / retain notices and inspect native dependencies'
        else:
            category = 'Unknown or ambiguous declaration / manual review required'
        entry['review_category'] = category
        site = root / '.runtime' / entry['group'] / 'Lib/site-packages'
        normalized = entry['name'].replace('-', '_').lower()
        folders = [p for p in site.glob('*.dist-info') if p.name.lower().startswith(normalized + '-')]
        entry['notice_files'] = [p.relative_to(root).as_posix() for folder in folders for p in folder.rglob('*')
                                 if p.is_file() and any(x in p.name.lower() for x in ('license', 'copying', 'notice', 'authors'))]
        categories[category] = categories.get(category, 0) + 1
    report = {'public_binary_cleared': False, 'method': 'Every packaged distribution metadata and retained notice filenames; native legal review incomplete',
              'categories': categories, 'packages': records}
    (root / 'docs/license-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(categories, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('build', type=Path)
    review(parser.parse_args().build.resolve())
