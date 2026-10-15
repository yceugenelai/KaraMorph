<div align="center">
  <img src="assets/icon_doc.png" width="112" alt="KaraMorph icon" />
  <h1>KaraMorph · カラモーフ</h1>
  <p>用 AI，讓熟悉的歌曲唱出新的風格。</p>
  <p>你喜愛的歌曲，你喜愛的風格，你專屬的歌唱清單。</p>
</div>

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

想唱的歌，卡拉 OK 偏偏沒收錄？或想換個曲風唱熟悉的歌？KaraMorph 是 Windows 卡拉 OK 應用程式，讓唱歌多一點自己的選擇。支援繁體中文、英文、日文介面。

### 歌曲處理

![歌曲處理](docs/images/processing-placeholder.png)

### 唱歌中

![唱歌中](docs/images/singing-placeholder.png)

<sub>截圖示範曲：<a href="https://commons.wikimedia.org/wiki/File:Amazing_Grace_US_Marine_Band.ogg">Amazing Grace — United States Marine Band</a>。Wikimedia Commons 標示此錄音與 John Newton 原版英文歌詞為公有領域。</sub>

## 主要功能與特色

- **自己的 MP3 變卡拉 OK**：自動分離人聲，產生可直接演唱的伴奏。
- **AI 重新編曲**：把同一首歌改成不同風格（例如酒廊爵士）。
- **歌詞**：自動搜尋歌詞，或手動輸入、貼上自己的歌詞。
- **背景畫面**：自動產生關鍵字搜尋唱歌時的背景圖，並可用簡單拖放為不同曲目指定背景。
- **即時調整**：唱歌中可調整 0.7–1.3 倍速度與 ±6 半音 Key。
- **歌單與錄音**：建立專屬歌單，並以麥克風錄下演唱。

## 測試環境

- Windows 11
- NVIDIA GeForce RTX 3050 Ti Laptop GPU（4 GB VRAM）
- 40 GB RAM

## 快速開始

以下以「把一首 MP3 變成爵士伴奏，再加入歌單唱歌」為例。不需要寫程式或安裝 Python。

1. **下載**：開啟 [Releases](https://github.com/yceugenelai/KaraMorph/releases)，在要使用的版本下展開 **Assets**，下載 `KaraMorph-bootstrap-poc.zip`。
2. **解壓並啟動**：對 ZIP 按右鍵 →「全部解壓縮」，放到可寫入的資料夾（如桌面），再雙擊 `KaraMorph.exe`。請勿直接在 ZIP 或 Program Files 內執行。
3. **完成首次安裝**：選擇介面語言，保留「人聲分離」與「ACE-Step」勾選，依畫面開始準備。需保持網路連線；含下載快取約需 35–40 GiB 空間。
4. **設定 input 資料夾**：在 `KaraMorph.exe` 所在資料夾建立 `input` 資料夾。進入「設定」，在「音樂資料夾」按「選擇…」選取它，再按「儲存設定」。
5. **放入一首歌**：將一首有權使用的 MP3 複製到 `input`，回到「處理歌曲」按「重新讀取」。
6. **分離人聲**：勾選歌曲，按「批次分離人聲」。完成後展開歌名即可看到「純伴奏」。
7. **找歌詞**：保持勾選，按「批次抓取歌詞」。找不到時，按該歌曲的「素材」手動搜尋或加入歌詞；沒有歌詞也能播放。
8. **改成喜歡的風格**：按歌曲右側的「製作版本」，「風格」選「酒廊爵士」，按「製作風格版本」並等待完成（AI 處理可能需要較長時間）。
9. **加入歌單**：展開歌曲，按完成版本（或「純伴奏」）右側的「加入歌單」。
10. **開始唱歌**：切到「唱歌」，選取歌曲，按「播放」。想保留歌單，按「儲存歌單」。

請使用已取得必要授權的歌曲、歌詞與影像，詳見下方[授權與法律提醒](#授權與法律提醒)。

## 授權

KaraMorph 自有原始碼採 [MIT](LICENSE)；相依套件、模型與原生執行檔有各自條款。不附音樂音檔與模型權重。其餘授權、散布與素材來源說明請見[補充資訊](#補充資訊)。

維護者網站：[OpticalPivot](https://www.opticalpivot.net/)。

---

## 補充資訊

### 授權與法律提醒

「自用」不代表一律合法；將改編歌曲放上網，可能需要重製、改作、公開演出、公開傳輸及錄音相關權利，依所在地法律與素材而定。取得 LRCLIB 歌詞也不代表取得散布授權。

- 精簡啟動 ZIP 的[散布範圍與檢查方式](docs/DISTRIBUTION.md)
- 截圖與影像素材的來源及權利：[素材聲明](docs/ASSET_CREDITS.md)、[影像素材授權範圍](assets/ASSET_LICENSE.md)
- 圖示由 OpenAI ChatGPT 生成；共用背景由 Google AI Studio 的付費 Nano Banana Pro 生成；截圖為 App 實際畫面。
- 程式碼與四張共用背景採 MIT；圖示保留權利，供 KaraMorph 專案使用；截圖內素材有各自權利。
- KaraMorph 的程式開發使用 OpenAI ChatGPT 與 Codex 協助。

### 背景圖片

預設共用背景放在 `outputs/shared_images/`（若更換輸出目錄，就放在該目錄下）。第一次使用會複製內附背景，你可以直接加入自己的圖片，也可以刪掉預設圖；自訂圖片不會被覆蓋。[新增背景圖片](assets/README.md)。

### 首次啟動設定

不需另裝 Python 或 Git。可選繁體中文、英文、日文；首次預設英文，之後沿用儲存的語言。人聲分離與 ACE-Step 預設皆勾選，會在本目錄準備 Python 環境、模型與固定版本 ACE-Step 程式碼；啟動 ZIP 大小不代表安裝後大小。

可取消勾選不需要的功能。基本播放及唱歌中的速度／Key 調整不需 AI 模型；首次準備會下載並驗證 SoundTouch。另存新的速度／Key 版本仍需 ACE-Step 執行環境。失敗或取消時保留完成項目，可重試或先使用已有功能；之後可從「設定 → 管理安裝」補裝。正常啟動不會補裝先前未勾選的功能。[設定與建置說明](docs/BOOTSTRAP.md)。

「模型與元件管理」會顯示 SoundTouch 的安裝與校驗狀態，可獨立下載／修復，不需要下載 AI 模型。

### 硬體與平台

AI 優先使用 NVIDIA GPU；VRAM 不足或歌曲太長時，確認後可改用 CPU，但時間較長且需要大量 RAM。此版不支援 AMD/Intel GPU 加速。主要平台為 Windows 11 x64；不同音效設備需實際測試。WASAPI 獨占播放會影響其他程式使用同一設備。

### 播放速度與 Key

速度 −／+ 每次調整 0.1 倍，「更多設定 → 播放速度／Key」保留速度滑桿供微調，也可重設速度與 Key。只作用於目前版本的歌曲與導唱，麥克風監聽／原始錄音維持原聲；每首歌重新開始時重設。歌詞會跟著歌曲來源時間前進。整首錄音會額外保存當時實際播放的伴奏 WAV，供之後同步試聽，因此需要額外磁碟空間。共用與獨占播放都支援此功能，不會製作新的歌曲版本。

### 處理清單與資料位置

處理清單會以音樂目錄的實際內容為準。新增、刪除或移走檔案後，按「重新讀取」即可同步；清單不再保留遺失的原曲，也不使用黑名單。處理結果、錄音與素材仍保留在磁碟上；歌單既有項目會標示為無法使用，原檔放回後可恢復。

設定、歌單、快取與紀錄位於 `.app_data/`，模型位於 `models/`，處理結果預設位於 `outputs/`。可在 UI 選擇模型與輸出目錄。語言切換後須重啟。更新採手動方式，先備份再保留資料目錄並替換程式。限制與公開前檢查請見 [RELEASE](docs/RELEASE.md)。

### 資料搬移

目前可更換程式目錄，但**不能只把歌曲與處理結果複製到新路徑，就保證原有版本、錄音與歌單仍能連上**。歌曲識別包含原曲的完整路徑、大小與修改時間；處理結果也記錄絕對路徑。

同一台電腦換到新解壓的版本，最簡單可靠的方式是保留原本資料的位置：

1. 關閉新舊 KaraMorph，先備份舊資料。保留原本音樂資料夾及整個處理結果資料夾（預設 `outputs/`）。
2. 將舊 `.app_data/settings.json`、`last_playlist.json` 複製到新程式的 `.app_data/`（若存在）；想保留診斷紀錄也可複製 `logs/`。自行另存的歌單 JSON 請另外保留。不用複製模型、Python 環境、快取或 `model-receipts.json`。
3. 啟動新程式，在「設定」將音樂資料夾與處理結果資料夾指定回**原本的完整路徑**，儲存設定，再按「重新讀取」。
4. 確認原曲、既有版本、錄音及歌單可使用，再整理舊程式。

若搬到另一台電腦，資料需放在與原本相同的磁碟代號與完整路徑，並保留原曲的檔名、內容與修改時間。目前尚無自動搬移工具，請保留備份。

### 打包精簡版 ZIP

在 Windows 11 x64 的原始碼目錄開啟 PowerShell，指定已安裝 PySide6 的 Python（準備方式見 [Python 環境與原始碼執行](docs/DEVELOPMENT.zh-TW.md)）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

產物為 `dist/KaraMorph-bootstrap-poc.zip`，不含執行環境、模型與個人資料。重新打包既有產物時加上 `-Repack`。[打包細節](docs/BOOTSTRAP.md)。

### 開發

Python 環境設定與直接執行原始碼的方式，請見[開發指南](docs/DEVELOPMENT.zh-TW.md)。
