# GitHub repository handoff

The exported `KaraMorph/` directory is the repository root, not a subfolder to put inside another `release_project/`. It includes application/worker code, translations, lockfiles, build scripts, tests, three README languages, notices and supplied assets. Local environments, models, ACE-Step checkout, songs, recordings, caches, logs, backups and build binaries are excluded.

## Local preparation

1. Move the exported folder to the location for the new repository, outside the old development checkout.
2. Read `docs/PUBLIC_REVIEW.md` and review the confirmed generation sources and confirmed asset licenses and the reviewed Amazing Grace screenshots in `docs/ASSET_CREDITS.md` before public publication. Review the MIT copyright line (`KaraMorph contributors`) and choose the repository URL/name.
3. Create an empty GitHub repository named `KaraMorph`; avoid auto-generating another README or LICENSE.
4. In the exported folder, run:

```powershell
git init -b main
git add .
git diff --cached --stat
git status --short
git commit -m "Initial KaraMorph source"
```

Inspect staged files before committing; no `.app_data`, `models`, `input`, `outputs`, `third_party`, `build`, `dist`, EXE, audio or credentials should be present. `.gitignore` protects subsequent local development data.

## Upload after local review

```powershell
git remote add origin https://github.com/YOUR_ACCOUNT/KaraMorph.git
git push -u origin main
```

Replace `YOUR_ACCOUNT` with your account. GitHub authentication is handled by your Git installation; never put a token into source files or remote URLs. These commands are instructions only: export performs no Git initialization, commit, push or publication.

## Reproduce and verify

Use the three-language `docs/DEVELOPMENT*.md` guides and `docs/BUILD.md`. After setup:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Tests use fixtures/synthetic data and do not require bundled music or weights. Builds need network for official downloads. Windows hardware/audio quality and a separate clean machine remain validation limits.

Attach the verified no-uv bootstrap ZIP and its SHA-256 to GitHub Releases; commit source only. The lightweight ZIP is the only application release package; see `docs/RELEASE.md` for the testing and publication checklist.
