# Lightweight ZIP release scope

## Distributed package

KaraMorph provides `KaraMorph-bootstrap-poc.zip`, without bundled runtimes, models or uv. It contains the application launcher, source, assets, documentation and notices. After extraction, `KaraMorph.exe` downloads and prepares the selected components from upstream.

The matching source ZIP, SHA-256 sidecars and generated ZIP inventory accompany each Release. See [DISTRIBUTION.md](DISTRIBUTION.md) for the archive scope and [BUILD.md](BUILD.md) for local and GitHub Actions builds.

## Release checks

1. Build from the intended commit and run `scripts/review_bootstrap_zip.py`. Retain source, asset and third-party notices; attach the checksums and matching source ZIP.
2. Extract the entire ZIP to a separate writable folder and test `KaraMorph.exe`, first-run preparation and the intended features. A separate Windows 11 x64 machine without development tools provides additional validation; offscreen tests do not certify every machine or audio device.
3. Confirm the version, release notes and current asset provenance, then manually publish the tested draft Release created by GitHub Actions.

Publish the generated archive. Keep initialized installation folders, downloaded dependencies, models, songs and user data out of release attachments.

## Included behavior

Three UI languages; playback, recording, lyrics and backgrounds; separate workers for vocal separation and styling. Fixed model hashes and ACE-Step revision, verified atomic downloads, resumable partial downloads, local model import and cancellation. Data stays under the application by default. Basic playback needs no model weights; key/speed processing needs the ACE-Step runtime. Settings → Manage installation prepares features later. Preparing models does not automatically resume an AI job.

## Known limitations

CPU styling can be slow and memory intensive. Exclusive audio depends on drivers; occasional glitches remain possible. Plain lyrics use approximate vocal activity. LRCLIB may lack tracks and requires network. Updates are manual; back up data first. After moving an existing installation, re-verify models; absolute references in playlists or external song paths may need reimport. Read-only installations are unsupported. Network-read cancellation can take up to 30 seconds.

No audio tracks or model weights are supplied in the ZIP. Downloaded components retain their own licenses and notices; the archive review does not grant permission to redistribute an initialized installation.
