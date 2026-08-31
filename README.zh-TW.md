# SCIONX 訊號解碼卡關紀錄 — 給社群的求助文

> 這是 [`README.md`](README.md)（公開在 GitHub 上、給英語社群看的版本）的中文對照版，
> 提供給看得懂中文的讀者參考，**不是**要取代公開版本；內容架構跟公開版本
> 完全對齊，方便交叉比對。

## 背景

這是一份 **GFSK/9600bps** 的音訊錄音（`.ogg`），內容是一顆 CubeSat
（SCIONX/RANGE A）的下行訊號，從 [SatNOGS Network](https://network.satnogs.org/)
（一個群眾外包的開放衛星地面站網路）下載下來的，我們需要從裡面解出
**HDLC/AX.25 frame**。已知這份錄音裡含有 2-3 個完整的 frame（其中一個的
header 已經對照過地面測試的參考封包驗證過，見 `REFERENCE_FRAME.md`），但
目前的 pipeline 卡在 CRC 檢查這一關：**0 個 frame 能通過 CRC**。

## 方法

整體處理分成三個步驟：

1. **Frame 偵測**：定位封包的位置，再把整段訊號切成
   header/data/zero-run/FCS 段落。
2. **Zero-run 段落**：處理填充區段的資料，標出裡面孤立的「1」。
3. **Data 段落**：處理真正含有 telemetry 內容的資料，套用閾值，標出判決線。

我們已經跑過一輪相當徹底的逐步診斷，排除了好幾個假設（baseline 曲率、
timing/取樣相位、AGC 增益追蹤……），但還是沒找到能讓任何一個 frame 真正通過
CRC 的修法。這是那次診斷工作裡最關鍵的部分，拆成三個獨立、可以各自重跑的
資料夾，希望社群能提供不同的角度。

目前 `03_data_segment_processing/` 裡在試的固定偏移閾值（從已知的 header
bit 校正出來）目前只做到「畫出判決線給人看」——還沒有實際驗證能讓任何一個
frame 通過 CRC（data 段落的內容每個 frame 都不一樣，沒有可靠的已知真值可以
拿來算 bit error rate）。

**想請教社群的問題**：
- data 段落的判決方法還有什麼可以改進的地方？
- 整套訊號處理 pipeline 有沒有更好的做法？

## 三個診斷資料夾

| 資料夾 | 主題 | 原始命名 |
|---|---|---|
| [`01_frame_detection/`](01_frame_detection/) | 自動偵測封包起點（正規化互相關），再把整段訊號切成 header/data/zero-run/FCS 段落；每個 frame 各輸出一張圖 | Phase 1e / 1e2 |
| [`02_zero_run_baseline/`](02_zero_run_baseline/) | 自動偵測每個 frame 之後，用 decision-directed baseline restoration + 兩種判決方法，標出 zero-run 段落裡孤立的「1」；每個 frame 各輸出一張圖 | Phase 7 |
| [`03_data_segment_processing/`](03_data_segment_processing/) | 自動偵測每個 frame 之後，從已知的 header bit 校正出固定偏移閾值：顯示不對稱性本身（03a）+ 套用判決線（03b）；每個 frame 各輸出一張圖 | Phase 9 / 10 |

那個資料夾裡還有第 4 支腳本 `03c_symbol_sync_timing.py`，試著用 GNU Radio
真正的 `symbol_sync`（Gardner/M&M timing recovery）取代其他三個資料夾都在用
的固定速率/固定相位取樣——見下面的「目前的結果」。這是整個 repo 裡唯一需要
GNU Radio 的腳本；其他部分刻意維持依賴很少。

## Beacon 欄位對照（`04_Beacon/`）

[`04_Beacon/`](04_Beacon/) 是不同性質的資料夾：不是診斷方法，而是 beacon
telemetry 欄位格式參考資料（`SCIONX_TLMnew.xlsx` + `SCIONX_enums.json`，
都是使用者提供的），加上一支腳本 `04_beacon_field_decode.py`，把每個偵測到
的 frame 解出來的 Info 欄位（256-byte 的 telemetry payload）對照這個格式，
輸出一份逐 frame、逐欄位、逐 bit 的試算表——低信心的 bit 會上色，一眼就能看出
哪些解出來的值值得信任。Frame 偵測的方法跟 01/02/03 一樣，也不是寫死只認得
`cut_first3.ogg`，可以指向這顆衛星的任何其他錄音——但偵測門檻
（`Z_THRESHOLD`）要注意：這個值只在 `cut_first3.ogg` 上校正過，換一份錄音不
一定適用。搭配用的 `04a_zscore_visualization.py` 可以畫出任何一份錄音的偵測
z-score 曲線，直接從圖上讀出合適的門檻；兩支腳本都可以用 `--z-threshold`
覆寫，建議的流程是先用 `04a` 畫圖，再用 `04_beacon_field_decode.py` 解碼。
細節跟範例輸出見 [`04_Beacon/README.md`](04_Beacon/README.md)。

## 調查新錄音（`05_GNURadio_Czechia/`）

[`05_GNURadio_Czechia/`](05_GNURadio_Czechia/) 把這個專案的解碼方法套用到
另一批不同來源的錄音（一位捷克同事提供的原始 IQ 錄音，不是 SatNOGS 下載的
`.ogg`），走一套乾淨、有編號的三步驟 pipeline：**05a** 把原始 IQ 錄音解調
成 `.wav`（需要 GNU Radio——整條 pipeline 裡唯一需要的一步）、**05b** 對
整份錄音做交叉相關找出候選幀，依 30 秒區塊跟極性分組，嘗試自動
CRC-16/X.25 解碼、**05c** 裁切某個區段，對 **05b** 沒能自動解出來的部分開啟
互動式手動解幀工作簿。完整 pipeline 跟已驗證的結果見
[`05_GNURadio_Czechia/README.md`](05_GNURadio_Czechia/README.md)。

這個資料夾自己的調查歷程（GNU Radio Symbol Sync 實驗、header 相關性定位、
前饋式時脈音 timing-recovery 嘗試）原本放在 `appendix_gnuradio/`，現在已經
被上面這套 pipeline 取代、從這個分支移除；完整內容保留在
`archive/appendix_gnuradio` 分支上。

## 怎麼跑

只需要四個套件——**不需要 GNU Radio**（見 `requirements.txt`）：

```bash
pip install -r requirements.txt   # numpy, matplotlib, soundfile, openpyxl
cd 01_frame_detection    # 或 02_.../ 03_.../ 04_Beacon/
python 01_frame_detection.py    # 換成你想跑的腳本
```

（唯一的例外是 `03_data_segment_processing/03c_symbol_sync_timing.py`，
需要另外裝一個有 GNU Radio 的 Python——見那個資料夾的 README。）

每支腳本都是獨立可執行的——直接讀取 `Data/cut_first3.ogg`，自己重新算一遍，
不依賴任何其他腳本的中間產出。跑一次會在**目前所在的資料夾**存一張新的 PNG
（`04_Beacon/04_beacon_field_decode.py` 則是存一份 `.xlsx`）（同名檔案會被
覆蓋/新增），並把量到的數字印在終端機上。

共用的程式碼（`scionx/` package、`_style.py` 的繪圖設定）放在這一層
（`Share/`）的根目錄；所有分類資料夾裡的腳本都會自動找到，不用另外複製。

## 其他參考資料

- [`REFERENCE_FRAME.md`](REFERENCE_FRAME.md)：同一顆衛星地面測試的已知真值
  封包 hex dump。前 16 bytes（address+control+PID）跟 CRC 在任何封包裡應該
  都一樣，可以拿來當硬性的對齊/驗證基準；但 Info 欄位（telemetry 內容）
  **每個封包都不一樣**，沒辦法拿來當 data 段落 bit error 的已知真值——這個
  但書在每個分類自己的 README 裡也都有重複提醒。

## 目前的結果

- **Frame 切段/偵測相當成功**：`01_frame_detection/` 的正規化互相關方法剛好
  落在 3 個已知的 frame 起點上（z-score 12-13，遠高於雜訊本底的 z<5，沒有
  誤判），疊圖之後 header/data/zero-run/FCS 的邊界標記都跟原始波形對得起來
  ——切段這一步本身不是問題所在。
- **Zero-run 段落：配合 frame 的內部結構，人工可以濾掉誤判**：
  `02_zero_run_baseline/` 顯示，光用目前的固定閾值（Method 1），zero-run
  段落誤判成 1 的比率只有 0.8-2.5%，而且這些誤判點的位置零星、分散。因為
  這兩個段落理論上應該全部是零，拿這個已知結構當基準，配合少數幾個「判成 1」
  的候選點，是真的有機會逐一人工核對、濾掉錯誤的 bit。
- **Data 段落的資料還是很亂**：`03_data_segment_processing/` 顯示，就算套用
  了 header 校正出來的固定偏移閾值，data1/data2 裡大約還有 2-7% 的判決結果
  會隨著閾值改變而變動，而且不對稱性的大小還會隨著段落內位置緩慢漂移
  （03a 的「隱藏曲線」）。目前看起來最可行的方向是：先找出「貼近判決線
  （閾值）」的 bit——這些是最容易出錯、最可疑的候選——再拿一個真正解出來的
  frame（例如已知正確答案的部分，像 header/address/CRC）當基準，人工核對、
  濾掉這些邊界情況造成的錯誤。
- **把 3 個 frame 的 Info 欄位對照 beacon 實際的 telemetry 格式解出來，確認
  header 是乾淨的，但 payload 不是**：`04_Beacon/04_beacon_field_decode.py`
  把每個 frame 256-byte 的 Info 欄位對照 `SCIONX_TLMnew.xlsx` 的欄位格式
  解碼。3 個 frame 裡，Dest/Src address + Control + PID 都剛好解出跟地面
  測試一致的預期值（`'BN0CU '` / `'BN0SCX'` / `0x03` / `0xF0`）——所以
  header 是完整的，Info 欄位前面沒有漏掉任何東西。但每個 frame 只有
  1.2-3.4% 的 bit（header+data+zero-run+FCS 共 2192 個裡的 26-75 個）被標記
  為低信心，而且 3 個 frame 裡沒有一個的傳送端 FCS 跟payload 算出來的
  CRC-16/X.25 對得上——也就是說，大部分 bit 都是**很有信心**地被判決出來的，
  只是不代表判得**正確**。單看判決信心低不夠解釋為什麼 CRC 一直過不了。
  見 [`04_Beacon/README.md`](04_Beacon/README.md)。
- **另一批不同來源的錄音（`05_GNURadio_Czechia/`）端對端解出了兩個真實的
  frame，而且完全沒有手動指定任何座標。** 對一份完整、沒裁切過的
  771 秒／3700 萬個 sample 錄音跑這套 pipeline，定位＋解碼那一步純粹靠波形
  交叉相關就找到並 CRC 解出兩個幀（header 跟 `REFERENCE_FRAME.md` 逐 byte
  吻合，callsign 正確）——沒有手動提供任何位置或時序參數。另一個 header
  相關性一樣強的候選還是解不出來：先前自動化的相位／極性搜尋量出 41% 的
  原始位元錯誤率，這個專案的手動、逐 bit 互動工具（逐幀校準門檻 + PHASE
  微調掃描）把這個數字壓到 2.9%——確實有進步，但離 AX.25 這種沒有前向糾錯
  的 CRC 需要的「幾乎零位元錯誤」還有距離。見
  [`05_GNURadio_Czechia/README.zh-TW.md`](05_GNURadio_Czechia/README.zh-TW.md#已驗證的具體發現)。
