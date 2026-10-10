<div align="center">
  <img src="assets/icon_doc.png" width="112" alt="KaraMorph icon" />
  <h1>KaraMorph · カラモーフ</h1>
  <p>用 AI，讓熟悉的歌曲唱出新的風格。</p>
  <p>你喜愛的歌曲，你喜愛的風格，你專屬的歌唱清單。</p>
</div>

[English](README.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

## 為什麼想做 KaraMorph？

有時候想唱的歌，卡拉 OK 裡偏偏沒有收錄；有時候，也想試試用不同曲風唱一首熟悉的歌。所以做了 KaraMorph，希望唱歌能多一點自己的選擇。也真的很希望現有的卡拉 OK 系統，能有這樣個人化的功能。

### 歌曲處理

![歌曲處理](docs/images/processing-placeholder.png)

### 唱歌中

![唱歌中](docs/images/singing-placeholder.png)

<sub>截圖示範曲：<a href="https://commons.wikimedia.org/wiki/File:Amazing_Grace_US_Marine_Band.ogg">Amazing Grace — United States Marine Band</a>。Wikimedia Commons 標示此錄音與 John Newton 原版英文歌詞為公有領域。</sub>

預設共用背景放在 `outputs/shared_images/`（若更換輸出目錄，就放在該目錄下）。第一次使用會複製內附背景，你可以直接加入自己的圖片，也可以刪掉預設圖；自訂圖片不會被覆蓋。[新增背景圖片](assets/README.md)。

Windows 卡拉 OK 應用程式：分離人聲、調整音高與速度、AI 風格變化、歌單、歌詞、背景與麥克風錄音。支援繁體中文、英文、日文。

## 測試環境

- Windows 11
- NVIDIA GeForce RTX 3050 Ti Laptop GPU（4 GB VRAM）
- 40 GB RAM

## 快速開始

以下以「把一首 MP3 變成爵士伴奏，再加入歌單唱歌」為例。不需要寫程式或安裝 Python。

1. **下載**：開啟 [Releases](https://github.com/yceugenelai/KaraMorph/releases)，在要使用的版本下展開 **Assets**，下載 `KaraMorph-bootstrap-poc.zip`。這個檔案就是可執行的應用程式包。
2. **解壓並啟動**：在檔案總管對 ZIP 按右鍵 →「全部解壓縮」，放到例如桌面的 KaraMorph 資料夾。打開解壓後的資料夾，找到並雙擊 `KaraMorph.exe`；請先解壓，才執行。
3. **完成首次安裝**：選擇介面語言，保留「人聲分離」與「ACE-Step」兩項勾選，依畫面開始準備。保持網路連線並等待完成；含下載快取約需 35–40 GiB 空間。
4. **設定 input 資料夾**：在 `KaraMorph.exe` 所在資料夾建立名為 `input` 的資料夾。進入 App 的「設定」，在「音樂資料夾」按「選擇…」並選取這個 `input`，再按「儲存設定」。處理結果資料夾保留預設即可。
5. **放入一首歌**：用檔案總管將一首有權使用的 MP3 複製到 `input`，例如 `我的歌曲.mp3`。回到「處理歌曲」按「重新讀取」，歌曲就會出現在列表中。
6. **分離人聲**：勾選歌曲左側的方框，按「批次分離人聲」並等待完成。展開歌名前的箭頭，即可看到「純伴奏」。
7. **找歌詞**：保持歌曲勾選，按「批次抓取歌詞」。若找不到，按該歌曲的「素材」，手動搜尋或加入自己的歌詞；沒有歌詞也能播放。
8. **改成喜歡的風格**：按歌曲右側的「製作版本」，在「風格」選「酒廊爵士」，速度先保持 1.0 倍、Key 保持 0，按「製作風格版本」並等待完成。AI 處理可能需要較長時間。
9. **加入歌唱清單**：展開歌曲，找到完成的爵士版本，按該版本右側的「加入歌單」。也可以選「純伴奏」加入，直接唱原本的曲風。
10. **開始唱歌**：切到「唱歌」，選取歌單中的歌曲，按「播放」。伴奏與歌詞畫面會開啟，現在就可以跟著唱；想保留歌單，按「儲存歌單」。

## 打包精簡版 ZIP

在 Windows 11 x64 的原始碼目錄開啟 PowerShell，指定已安裝 PySide6 的 Python（準備方式見 [Python 環境與原始碼執行](docs/DEVELOPMENT.zh-TW.md)）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-bootstrap-poc.ps1 -UiPython C:\path\to\python.exe
```

產物為 `dist/KaraMorph-bootstrap-poc.zip`，不含執行環境、模型與個人資料。將整個 ZIP 解壓到另一個可寫入的目錄，再執行其中的 `KaraMorph.exe`。重新打包既有產物時加上 `-Repack`。[打包細節](docs/BOOTSTRAP.md)。

## 首次啟動設定

將精簡啟動 ZIP 完整解壓到可寫入的目錄，再執行 `KaraMorph.exe`。不需另裝 Python 或 Git，請勿直接在 ZIP 或 Program Files 內執行。

可選繁體中文、英文、日文；首次預設英文，之後沿用儲存的語言。人聲分離與 ACE-Step 預設皆勾選，會在本目錄準備 Python 環境、模型與固定版本 ACE-Step 程式碼。兩項功能含下載快取約需 35–40 GiB；啟動 ZIP 大小不代表安裝後大小。

可取消勾選不需要的功能。基本播放不需模型；目前調 key／速度仍需要 ACE-Step 執行環境。失敗或取消時保留完成項目，可重試或先使用已有功能；之後可從「設定 → 管理安裝」補裝。正常啟動不會補裝先前未勾選的功能。[設定與建置說明](docs/BOOTSTRAP.md)。

AI 優先使用 NVIDIA GPU；VRAM 不足或歌曲太長時，確認後可改用 CPU，但時間較長且需要大量 RAM。此版不支援 AMD/Intel GPU 加速。主要平台為 Windows 11 x64；不同音效設備需實際測試。WASAPI 獨占播放會影響其他程式使用同一設備。

## 日常使用

處理清單會以音樂目錄的實際內容為準。新增、刪除或移走檔案後，按「重新讀取」即可同步；清單不再保留遺失的原曲，也不使用黑名單。處理結果、錄音與素材仍保留在磁碟上；歌單既有項目會標示為無法使用，原檔放回後可恢復。

設定、歌單、快取與紀錄位於 `.app_data/`，模型位於 `models/`，處理結果預設位於 `outputs/`。可在 UI 選擇模型與輸出目錄。語言切換後須重啟。更新採手動方式，先備份再保留資料目錄並替換程式。限制與公開前檢查請見 [RELEASE](docs/RELEASE.md)。

請使用已取得必要授權的歌曲、歌詞與影像。「自用」不代表一律合法；將改編歌曲放上網，可能需要重製、改作、公開演出、公開傳輸及錄音相關權利，依所在地法律與素材而定。取得 LRCLIB 歌詞也不代表取得散布授權。

Python 環境設定與直接執行原始碼的方式，請見[開發指南](docs/DEVELOPMENT.zh-TW.md)。

## 資料搬移

目前可更換程式目錄，但**不能只把歌曲與處理結果複製到新路徑，就保證原有版本、錄音與歌單仍能連上**。歌曲識別包含原曲的完整路徑、大小與修改時間；處理結果也記錄絕對路徑。設定雖會調整部分程式目錄內的路徑，並不等於完整資料搬移。

同一台電腦換到新解壓的版本，最簡單可靠的方式是保留原本資料的位置：

1. 關閉新舊 KaraMorph，先備份舊資料。保留原本音樂資料夾及整個處理結果資料夾（預設 `outputs/`）；其中包含分離結果、風格版本、錄音、歌詞、圖片與共用圖片。
2. 將舊 `.app_data/settings.json`、`last_playlist.json` 複製到新程式的 `.app_data/`（若存在）；想保留診斷紀錄也可複製 `logs/`。自行另存的歌單 JSON 請另外保留。不用複製模型、Python 環境、快取或 `model-receipts.json`。
3. 啟動新程式，在「設定」將音樂資料夾與處理結果資料夾指定回**原本的完整路徑**，儲存設定，再按「重新讀取」。例如新程式放在 `D:\KaraMorph-new`，資料仍使用 `D:\KaraMorph-old\input` 與 `D:\KaraMorph-old\outputs`。
4. 確認原曲、既有版本、錄音及歌單可使用，再整理舊程式；保留仍在使用的資料夾。

若搬到另一台電腦，資料需放在與原本相同的磁碟代號與完整路徑，並保留原曲的檔名、內容與修改時間，再依上述方式設定。若要改變資料路徑，目前尚無自動搬移工具；只重新指定資料夾不足以修復全部關聯，請保留備份。

## 授權

KaraMorph 的程式開發使用 OpenAI ChatGPT 與 Codex 協助。

KaraMorph 自有原始碼採 [MIT](LICENSE)。相依套件、模型與原生執行檔有各自條款。精簡啟動 ZIP 的[散布範圍與檢查方式](docs/DISTRIBUTION.md)已另外整理。不附音樂音檔與模型權重。截圖與影像素材的來源及權利另見[素材聲明](docs/ASSET_CREDITS.md)。

圖示由 OpenAI ChatGPT 生成；共用背景由 Google AI Studio 的付費 Nano Banana Pro 生成；截圖為 App 實際畫面。來源與授權範圍見[素材聲明](docs/ASSET_CREDITS.md)。

程式碼與四張共用背景採 MIT；圖示保留權利，供 KaraMorph 專案使用；截圖內素材有各自權利。[影像素材授權範圍](assets/ASSET_LICENSE.md)。

維護者網站：[OpticalPivot](https://www.opticalpivot.net/)。
