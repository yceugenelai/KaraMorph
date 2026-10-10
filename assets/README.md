# Adding background images / 新增背景圖片 / 背景画像の追加

## English

After extracting the ZIP and using backgrounds for the first time, add images directly to `outputs/shared_images/` in the extracted application folder. If you changed the output directory, use its `shared_images/` folder instead. Images become available on the next song/background load; no JSON editing is needed.

Supported formats: SVG, PNG, JPEG, WebP and BMP. Subfolders are not scanned. Your images are never overwritten, and deleted defaults stay deleted.

Open **Assets → Images** and choose **Song image selection** or **Shared image selection** to import, paste or drop images. Images are copied into the corresponding folder immediately and remain after closing or canceling the editor. Each has its own image search tab, which opens DuckDuckGo or Google in your default browser; copy or drag your chosen image to the receiver bar. Shared images are available to all songs; song images follow your karaoke background settings. Save applies thumbnail settings and image deletions without closing the editor; cancel keeps images marked for deletion. Imported original files are left unchanged.

To include extra default backgrounds when building from source, put images directly in `assets/backgrounds/` before packaging. Use new filenames to avoid conflicts with existing user images.

## 繁體中文

ZIP 解壓並首次使用背景功能後，將圖片直接放入解壓目錄的 `outputs/shared_images/`。若更換輸出目錄，請使用該目錄下的 `shared_images/`。下次載入歌曲或背景時即可使用，不需修改 JSON。

支援 SVG、PNG、JPEG、WebP 與 BMP，不掃描子目錄。自訂圖片不會被覆蓋；已刪除的預設背景也不會重新加入。

開啟 **素材 → 圖片**，在「歌曲圖片選取」或「共用圖片選取」匯入、貼上或拖曳圖片，完成後立即複製到對應資料夾，關閉或取消素材編輯也會保留。兩者各有網路圖片搜尋分頁，會用預設瀏覽器開啟 DuckDuckGo 或 Google；選好圖後複製或拖到接收列即可匯入。共用圖片可供所有歌曲使用，歌曲圖片依唱歌背景設定使用。儲存會套用縮圖設定與刪除，並留在素材頁；取消則保留標記刪除的圖片。匯入來源的原始圖片檔案不變。

若要在原始碼打包時附上更多預設背景，請先將圖片放入 `assets/backgrounds/`，並使用新檔名以避免與使用者現有圖片衝突。

## 日本語

ZIP を展開して背景機能を初めて使用した後、展開先の `outputs/shared_images/` に画像を直接追加してください。出力先を変更した場合は、その中の `shared_images/` を使います。次に曲や背景を読み込むと利用できます。JSON の編集は不要です。

SVG、PNG、JPEG、WebP、BMP に対応します。サブフォルダーは読み込みません。ユーザーの画像は上書きされず、削除した既定の背景も再追加されません。

**素材 → 画像** の「楽曲画像の選択」または「共通画像の選択」で取り込み、貼り付け、またはドロップすると、対応するフォルダーにすぐコピーされます。閉じたりキャンセルしたりしても取り込んだ画像は保持されます。それぞれにネット画像検索タブがあり、既定のブラウザーで DuckDuckGo または Google を開きます。選んだ画像をコピーするか受信バーにドラッグしてください。共通画像はすべての曲で利用でき、楽曲画像はカラオケ背景の設定に従って表示されます。保存するとサムネイル設定と削除を反映し、素材ウィンドウは開いたままになります。キャンセルすると削除対象の画像は保持されます。取り込み元の画像ファイルは変更されません。

ソースからビルドする際に既定の背景を追加するには、パッケージ作成前に `assets/backgrounds/` へ画像を直接配置してください。既存のユーザー画像との競合を避けるため、新しいファイル名を使ってください。
