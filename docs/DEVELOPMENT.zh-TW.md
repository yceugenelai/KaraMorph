# Python 環境與原始碼執行

[English](DEVELOPMENT.md) · [繁體中文](DEVELOPMENT.zh-TW.md) · [日本語](DEVELOPMENT.ja.md)

ZIP 解壓後直接執行 `KaraMorph.exe`。以下供 Windows 11 x64 開發者在原始碼目錄操作。

## 精簡打包用 Python

只需已安裝 Python 與 PySide6，即可產生圖示並打包，不必準備完整 AI 開發環境。例如：

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install PySide6
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython .\.venv-build\Scripts\python.exe
```

建置也需要 Windows .NET Framework C# 編譯器。產物為 `dist/KaraMorph-bootstrap-poc.zip`；更新既有打包目錄時加上 `-Repack`。見[打包細節](BOOTSTRAP.md)。

## 直接執行原始碼

先準備可用 `python` 呼叫的 Python、網路與足夠磁碟空間：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -SkipModels
.\.venv\Scripts\python.exe run_app.py
```

設定腳本會準備專案自己的 Python 3.10／3.11、固定版本套件與 ACE-Step 程式碼。即使指定 `-SkipModels`，仍會準備所有開發環境，下載量可能達數 GiB。模型可之後透過 UI 準備。

精簡啟動 ZIP 打包與 Release 草稿流程見 [BUILD.md](BUILD.md)。回到[使用指南](../README.zh-TW.md)。
