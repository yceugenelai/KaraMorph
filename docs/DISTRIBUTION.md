# Lightweight ZIP distribution

## Application release artifact

KaraMorph provides only `KaraMorph-bootstrap-poc.zip` (without bundled uv). Publish the matching source ZIP and SHA-256 sidecars alongside it. This ZIP contains KaraMorph's own launcher, Python source, supplied visual assets, documentation and license texts. It contains no Python runtime, Qt/FFmpeg/CUDA libraries, model weights, music, lyrics or user settings. The MIT copyright and permission notice is included in `LICENSE`. The launcher source is supplied as `scripts/BootstrapPoc.cs`.

On first launch the user downloads uv from Astral's pinned official release, then Python, pinned packages, ACE-Step source and models from their upstream hosts. These downloads have their own licenses; retain upstream installed notices. The ZIP review covers what we distribute, not permission to republish the downloaded installation. Do not upload an initialized folder or mirror dependency downloads as part of this package.

## Checks and reproducible packaging

```powershell
.\.venv\Scripts\python.exe scripts/build_bootstrap_poc.py --repack
.\.venv\Scripts\python.exe scripts/export_source.py
.\.venv\Scripts\python.exe scripts/review_bootstrap_zip.py dist/KaraMorph-bootstrap-poc.zip
```

The review checks CRC, exact current-source membership, source/notice/assets presence, excludes user data/runtime/model folders, and permits only the KaraMorph launcher as a binary in the release ZIP. It emits a per-file SHA-256 inventory beside the ZIP. This is an artifact verification, not a legal certification. Build a fresh launcher from current source when launcher code changes; repack reuses the existing seed executable.

## Before announcing

The current cover-free processing capture and Amazing Grace original-English-lyrics capture were reviewed on 2026-10-07; their source and license scope are recorded in ASSET_CREDITS.md. If screenshots or assets change, review their embedded content again. Choose the public repository URL and release tag, and upload the verified ZIP, checksum, matching source ZIP and source checksum. Keep `LICENSE`, `THIRD_PARTY_NOTICES.md`, `docs/licenses/` and this document intact. The local packaging scripts do not publish remotely; the manually triggered GitHub Actions workflow creates a draft Release for testing before publication.

References: [uv MIT](https://github.com/astral-sh/uv/blob/0.12.21/LICENSE-MIT), [uv Apache-2.0](https://github.com/astral-sh/uv/blob/0.12.21/LICENSE-APACHE), [FFmpeg legal](https://www.ffmpeg.org/legal.html), [Qt licensing](https://doc.qt.io/qtforpython-6/licenses.html).
