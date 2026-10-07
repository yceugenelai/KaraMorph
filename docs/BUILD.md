# Build the lightweight Windows ZIP

KaraMorph provides one application package: `KaraMorph-bootstrap-poc.zip`, without bundled runtimes, models or uv. After extraction, users run `KaraMorph.exe`; first-run setup downloads the selected components from upstream.

## Local build

On Windows 11 x64, use a Python environment with PySide6 and the Windows .NET Framework C# compiler. See the [developer guide](DEVELOPMENT.md) for Python preparation.

From the source folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

Output: `dist/KaraMorph-bootstrap-poc.zip` and its SHA-256 sidecar. Existing staging is preserved; add `-Repack` to rebuild an existing package. The script recompiles the launcher before packaging.

Verify the archive using the same Python:

```powershell
C:\path\to\python.exe scripts/review_bootstrap_zip.py dist/KaraMorph-bootstrap-poc.zip
```

See [BOOTSTRAP.md](BOOTSTRAP.md) for setup details and [DISTRIBUTION.md](DISTRIBUTION.md) for included files and notices.

## GitHub Actions

Push the workflow to the default branch. In Actions, select **Build release → Run workflow**, choose the branch and enter a new version tag such as `v0.1.0-preview.1`. No YAML edit is needed for each version.

The workflow builds and verifies the lightweight ZIP, exports matching source, and creates the tag and a draft Release with archives, checksums and the ZIP inventory. Extract and test the application ZIP in a separate writable folder, then publish the draft manually. Existing tags or releases are not overwritten.

## Source export

Source archives accompany application releases for source and license access; they are not another application package.

```powershell
C:\path\to\python.exe scripts/export_source.py
```

This creates `dist/KaraMorph-0.1.0-preview-source.zip` and a checksum; the release workflow names the source archive using the entered version. Export excludes local environments, models, music, settings, caches and binary builds. Review the file list before committing or uploading.

To create a fresh source directory:

```powershell
C:\path\to\python.exe scripts/export_source.py --directory dist/source-repository/KaraMorph
```

The destination must not already exist. It contains no Git history. Initialize or publish it separately; see [REPOSITORY.md](REPOSITORY.md). The local exporter does not contact GitHub.
