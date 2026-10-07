# Python 環境とソースからの実行

[English](DEVELOPMENT.md) · [繁體中文](DEVELOPMENT.zh-TW.md) · [日本語](DEVELOPMENT.ja.md)

ZIP を展開した場合は `KaraMorph.exe` を起動してください。以下は Windows 11 x64 の開発者向けで、ソースフォルダーから実行します。

## 軽量ビルド用の Python

Python と PySide6 があればアイコン生成とパッケージ作成ができます。完全な AI 開発環境は不要です。例：

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install PySide6
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython .\.venv-build\Scripts\python.exe
```

Windows .NET Framework C# コンパイラーも必要です。出力は `dist/KaraMorph-bootstrap-poc.zip`。既存のビルド用フォルダーを更新する場合は `-Repack` を追加します。[ビルドの詳細](BOOTSTRAP.md)を参照してください。

## ソースからの実行

`python` コマンドで使える Python、インターネット接続、十分なディスク容量を用意してください：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -SkipModels
.\.venv\Scripts\python.exe run_app.py
```

セットアップは専用の Python 3.10／3.11、固定版パッケージ、ACE-Step ソースを準備します。`-SkipModels` を指定してもすべての開発環境を準備するため、数 GiB のダウンロードが必要になる場合があります。モデルは後から UI で準備できます。

軽量 ZIP のビルドと Release 下書きの作成は [BUILD.md](BUILD.md) を参照してください。[ユーザーガイド](../README.ja.md)に戻る。
