# 第一階段模型與上游來源聲明

固定 revision、下載來源與 SHA256 見 [model-assets.lock.json](model-assets.lock.json)。本文件補充已採用的元件；早期授權查核見 [第三方授權與散布檢查](../THIRD_PARTY_NOTICES.md)。

- **Kim Mel-Band RoFormer 權重**：原作者 KimberleyJSN，revision `ac9b0614ab3cd7f77219e18ba494dfd93956c348`；原作者 model card 標 MIT，保存於 [licenses/Kim-model-card.md](licenses/Kim-model-card.md)，原始來源連結見資產 lock。不是其他作者的微調權重。配對 YAML 來源及 checksum 亦鎖定。
- **ACE-Step 1.5 程式碼**：revision `ca1e85fe9430179831e6bc6be790c332190a3866`，保存原始 [MIT LICENSE](licenses/ACE-Step-1.5-LICENSE)。上游各檔案原有的版權及 Apache 標頭保留。
- **ACE-Step 官方 turbo／VAE 資產**：revision `19671f406d603126926c1b7e2adc169acbcade22`；官方 model card 標 MIT，與權重一起保存在 `models/acestep/README.md`。沒有加入其他來源權重。
- **Qwen3-Embedding-0.6B**：官方 model card 標 Apache-2.0；保存[授權宣告及來源](licenses/Qwen-model-license.md)與 [Apache-2.0 全文](licenses/Apache-2.0.txt)。目前 embedding 檔案來自固定 ACE-Step 官方資產 snapshot，hash 已核對。

新版不使用 Viperx ep368、htdemucs 權重，也不下載替代四軌模型。本文件涵蓋上述模型來源，並非整個 Python／FFmpeg 相依套件的完整發佈授權清單。精簡啟動 ZIP 不含執行環境與模型，首次啟動從上游下載的元件仍須保留各自條款及適用 notices。模型授權不授予來源歌曲的使用或改編權。
