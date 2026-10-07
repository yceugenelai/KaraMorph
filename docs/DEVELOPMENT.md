# Python setup and running from source

[English](DEVELOPMENT.md) · [繁體中文](DEVELOPMENT.zh-TW.md) · [日本語](DEVELOPMENT.ja.md)

For the extracted ZIP, run `KaraMorph.exe`. The following is for developers on Windows 11 x64, from the source folder.

## Python for lightweight packaging

An installed Python with PySide6 is enough for icon rendering and packaging; the full AI development environments are unnecessary. For example:

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install PySide6
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython .\.venv-build\Scripts\python.exe
```

The build also needs the Windows .NET Framework C# compiler. Output: `dist/KaraMorph-bootstrap-poc.zip`. Add `-Repack` to refresh existing staging. See [bootstrap build details](BOOTSTRAP.md).

## Running from source

Start with an installed Python available as `python`, internet access and sufficient disk space:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -SkipModels
.\.venv\Scripts\python.exe run_app.py
```

Setup installs project-owned Python 3.10/3.11, pinned dependencies and ACE-Step source. Even with `-SkipModels`, this prepares all development runtimes and can download several gigabytes. Prepare models later through the UI.

For lightweight ZIP builds and draft Releases, see [BUILD.md](BUILD.md). Return to the [user guide](../README.md).
