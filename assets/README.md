# Adding background images / 新增背景圖片 / 背景画像の追加

## English

After extracting the ZIP and using backgrounds for the first time, add images directly to `outputs/shared_images/` in the extracted application folder. If you changed the output directory, use its `shared_images/` folder instead. Images become available on the next song/background load; no JSON editing is needed.

Supported formats: SVG, PNG, JPEG, WebP and BMP. Subfolders are not scanned. Your images are never overwritten, and deleted defaults stay deleted.

To include extra default backgrounds when building from source, put images directly in `assets/backgrounds/` before packaging. Use new filenames to avoid conflicts with existing user images.

## 繁體中文

ZIP 解壓並首次使用背景功能後，將圖片直接放入解壓目錄的 `outputs/shared_images/`。若更換輸出目錄，請使用該目錄下的 `shared_images/`。下次載入歌曲或背景時即可使用，不需修改 JSON。

支援 SVG、PNG、JPEG、WebP 與 BMP，不掃描子目錄。自訂圖片不會被覆蓋；已刪除的預設背景也不會重新加入。

若要在原始碼打包時附上更多預設背景，請先將圖片放入 `assets/backgrounds/`，並使用新檔名以避免與使用者現有圖片衝突。

## 日本語

ZIP を展開して背景機能を初めて使用した後、展開先の `outputs/shared_images/` に画像を直接追加してください。出力先を変更した場合は、その中の `shared_images/` を使います。次に曲や背景を読み込むと利用できます。JSON の編集は不要です。

SVG、PNG、JPEG、WebP、BMP に対応します。サブフォルダーは読み込みません。ユーザーの画像は上書きされず、削除した既定の背景も再追加されません。

ソースからビルドする際に既定の背景を追加するには、パッケージ作成前に `assets/backgrounds/` へ画像を直接配置してください。既存のユーザー画像との競合を避けるため、新しいファイル名を使ってください。
