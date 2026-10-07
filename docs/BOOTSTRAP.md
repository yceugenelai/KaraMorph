# Bootstrap setup and build

## Using the package

Extract the complete bootstrap ZIP into a writable folder and run `KaraMorph.exe`. Windows 11 x64 is the primary target. No installed Python or Git is needed. Internet is required during preparation.

Select English, Traditional Chinese or Japanese. First launch defaults to English; an existing installation uses the saved language. Vocal separation and ACE-Step are both checked by default. Each selected group prepares a pinned Python environment and verified model files; styling also prepares the pinned ACE-Step source. Allow approximately 35–40 GiB for both features including caches. The small download package does not include the final installed environments or models.

Basic playback does not need either AI group. Key/speed processing currently uses the ACE-Step runtime. Cancel preserves completed items. Failed items can be retried, or the basic UI can be used once it is ready. Normal launches use saved choices; unchecked features are not installed automatically. Use Settings → Manage installation to add features later.

## Storage and troubleshooting

`.runtime/`, `.tools/`, `.app_data/`, `models/`, `third_party/` and `outputs/` are local installation/data folders. The FFmpeg executable provided by the downloaded imageio-ffmpeg package is exposed through a worker-local PATH under `.tools/ffmpeg/`; system FFmpeg is not required. Settings, caches and logs stay in the project by default.

Logs: `.app_data/logs/bootstrap.log`. A checksum mismatch is a failure: files are activated only after their complete SHA256 matches the pinned manifest. Existing verified models are reused. Interrupted model files may resume; an interrupted uv archive restarts. Network-read cancellation can take up to 30 seconds. Keep a writable folder and enough free disk space.

For a manual update, close the application and setup window, back up data, then replace program files while preserving local environment/model/data folders. If an earlier setup failed, open Manage installation and retry. No automatic application update is provided.

## Building from source

The source repository intentionally contains no precompiled launcher or uv executable. Windows .NET Framework C# compiler is used to build the launcher. A developer Python with PySide6 can run the packaging script and render the executable icon; preparing the full AI development environment is not necessary. See [Python setup](DEVELOPMENT.md).

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

The artifact is `dist/KaraMorph-bootstrap-poc.zip`. uv is downloaded from upstream on first launch; it is not bundled. Its pinned version, official URL and archive SHA256 are constants in `scripts/BootstrapPoc.cs`.

Prepared local staging directories are preserved. To refresh an existing package after code changes, use `python scripts/build_bootstrap_poc.py --repack`. If the launcher source changed, recompile it first. Repacking excludes runtimes, models, caches, logs and user data.

See [acceptance results](ACCEPTANCE.md) and [distribution review](../THIRD_PARTY_NOTICES.md). Verify the archive and test the extracted application before publishing; see [release checks](RELEASE.md).

## Adding backgrounds

See [the background guide](../assets/README.md) for adding your own images or including extra default backgrounds in a source build.
