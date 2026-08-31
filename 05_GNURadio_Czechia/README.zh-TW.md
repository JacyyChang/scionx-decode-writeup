# 05 — GNU Radio Czechia pipeline：cs16 IQ 錄音 → wav → 定位/解碼 → 手動解幀

## 這個資料夾是什麼

這裡處理的錄音（`Data/*.cs16`，原始 100 kHz complex-short IQ）是一位捷克
同事提供的——跟 `cut_first3.ogg`、以及早期 `appendix_gnuradio/` 診斷 session
自己抓的 SatNOGS 錄音是不同來源，但同一顆衛星／同一套協定（SCION-X /
RANGE-A，GFSK 9600，AX.25/HDLC——見專案根目錄 `README.md`）。

`appendix_gnuradio/` 是這些新錄音的**調查過程**發生的地方（一份持續累積的
紀錄：訊號強度掃描、Symbol Sync 實驗、header 相關性定位、`Recovery_try/`
的時脈音時序回復嘗試）。這個資料夾則是那次調查的**成果**，整理成一套乾淨、
可重複使用、有編號的 pipeline——跟 `01_frame_detection/` → `04_Beacon/`
同一套慣例，只是輸入從已經解調好的 `.ogg` 換成原始 IQ。

只有 **Step 1（05a）**需要 GNU Radio（radioconda 環境 + PyQt5）。Step
2–3 全部是純 Python（`numpy` + `soundfile`，跟這個 repo 其他部分同一套最小
依賴）加上 Step 2 定位那半用的 MATLAB——一旦有了 `.wav`，就不再需要 GNU
Radio。

## 目錄結構

```
05a_IQtoOgg_plot.grc / .py   Step 1：cs16 IQ -> 48k wav + 週期性頻譜圖 PNG
s05b_header_locate_decode.m  Step 2（MATLAB 端）：定位每個候選 header，依
                              （30 秒 "seg"、極性）分組，呼叫 Python 端
05b_decode_at_grid.py        Step 2（Python 端）：在指定 (sps, phase) 做
                              CRC-16/X.25 解碼，可限制在某個候選點附近的窗口
05c_destuff_interactive.py   Step 3：裁切某個 seg（可選）+ 互動式手動解幀
                              工作簿，處理 Step 2 沒 CRC 過的候選
grc_blocks/                  gr_plot_capture.py、gr_plot_sink.py（Step 1
                              繪圖用的函式庫）+ plot_capture.block.yml
                              （GRC block 定義）
gr_plot_capture.py           2 行轉接檔，讓 05a 裡單純的 `import
                              gr_plot_capture` 不管 GRC 自己的 block 定義
                              快取狀態如何都能動（見 Gotchas）
Data/   *.cs16 原始 IQ 錄音（Step 1 輸入）
Figure/ 週期性頻譜圖 PNG（Step 1 輸出，每 30 秒一張）
Output/ wav / xlsx / fig / png（Step 1 之後所有下游產出）
```

## Pipeline 流程

### Step 1（05a）：IQ → wav + 頻譜圖

改 `05a_IQtoOgg_plot.grc` 最上面的 `cs16_path`（直接跑 `.py` 的話改
`05a_IQtoOgg_plot.py` 裡同名變數）指向要處理的錄音，然後執行：

```bash
C:\Users\USER\radioconda\python.exe -u 05a_IQtoOgg_plot.py
```

這是一個**真的、有 GUI、即時（real-time）步調**的流程圖（裡面的
`Throttle` block 會把速度卡在錄音本身的取樣率）——處理一段 771 秒的錄音要
跑滿約 13 分鐘的真實時間，而且**不會自動關閉**：要自己手動關掉視窗（否則
wav 的 RIFF header 不會正確收尾，見 Gotchas）。輸出：
`Output/<rec_stem>_48k.wav`（32-bit float，跟來源訊號本身的動態範圍一致——
這裡 GFSK 解調後的音訊振幅可以到 ±60–80，離正規化的 ±1 很遠）以及
`Figure/<rec_stem>_seg###_spec_*.png`，每 30 秒一張。

### Step 2（05b）：對**整份錄音**定位＋解碼

直接對 `05a` 輸出的完整、沒裁切過的檔案跑——不用先看頻譜圖猜哪一段：

```matlab
WAV = 'Output/<rec_stem>_48k.wav';   % 改 s05b_header_locate_decode.m 最上面這行
run('s05b_header_locate_decode.m')
```

這支會拿已知的 720-sample header 樣板（4 個 flag + 14-byte 的 Dest+Src
位址——這顆衛星每個 frame 都一樣）對整段波形做交叉相關，把找到的候選點依
`（30 秒區塊 "seg"、極性）`分組，對每一組呼叫一次 `05b_decode_at_grid.py`
——限制在該組錨點附近約 22000 個 sample 的窗口，這正是「這個解出來的 frame
到底屬於哪個 seg」能被回答的關鍵（底層的 HDLC 解幀器本身完全不知道自己在
bitstream 裡的位置；限制搜尋窗口才能把結果釘死在某一個候選點上）。會印出
並畫出每個 seg 的 PASS/FAIL 摘要。

### Step 3（05c）：裁切（可選）+ 手動解幀，處理沒過的候選

對 Step 2 標出來（有找到、但沒 CRC 過）的 seg：

```bash
python 05c_destuff_interactive.py Output/<rec_stem>_48k.wav --seg 10
```

會裁切出那 30 秒的窗口（存進 `Output/`，命名方式跟 Step 2 自己的 seg
標籤一致），並立刻用 `04_Beacon/04d_destuff_interactive_ver2.py` 建立起來的
那套互動式、逐 bit 解幀工作簿打開它——`LowConfidence`/`CandidateStuffPoint`
提示、`ExcludeThisBit`/`FlipThisBit` 下拉選單、即時用 Excel 公式算的
CRC-16/X.25 檢查。加 `--crop-only` 就只拿 wav 檔（給
`eye_fixed_grid.m`、`header_correlate_locate.m`，或任何其他需要小段
per-segment 檔案、但不需要這個工具的場合用）。

## 已驗證的具體發現

- **整套 pipeline 可以端對端解出真實的幀，而且完全不用手動指定任何座標。**
  對一份完整 771 秒／3700 萬個 sample 的錄音跑一次，Step 2 純粹靠波形交叉
  相關就找到並 CRC 解出兩個幀（header 跟 `REFERENCE_FRAME.md` 逐 byte
  吻合，callsign 正確解出 `BN0CU`/`BN0SCX`）——事前完全不知道它們在哪裡。
  總耗時約 66 秒（6 秒相關運算 + 5 次窗口化解碼呼叫，每次約 12 秒），對比
  被取代的舊設計（每個極性都掃整個檔案）要約 230 秒。
- **極性錯誤的相關性旁瓣穩定地解不出來。** 同一次跑出來的 5 個候選群組裡，
  有兩個是真實幀自己旁瓣的相反極性（同一個 seg、z 值較弱）——兩者都正確地
  回報 `NO_CRC_PASS`，證明分組＋解碼這一步真的在區分真訊號跟虛假相關性，
  不是只會回聲最強的候選點。
- **Step 3 的手動工具，對那個還是解不出來的候選（seg010）明顯優於 Step 2
  的盲目搜尋。** `Recovery_try/` 之前的自動化相位／極性／切片器掃描，對這個
  候選量出 41% 的 header 位元錯誤率。`05c_destuff_interactive.py` 用
  逐幀校準的門檻（從這個候選自己的 header 樣本擬合出來，不是假設一個
  sign 門檻）加上 PHASE 微調掃描，把這個數字壓到**在 1320 個可驗證
  （header + zero-run）bit 位置上只錯 2.9%**——確實有進步，但離 AX.25
  這種沒有前向糾錯的 CRC 需要的「幾乎零位元錯誤」還有距離。這個結果是在
  **精煉**、而不是推翻先前「訊噪比限制」的結論。

## Gotchas

- **GNU Radio Companion 只在啟動時重新掃描一次 `local_blocks_path`，實測
  重開也不見得會真的重新讀到。** 所以 `05a` 的 Plot Capture block**沒有**
  靠自己的 `imports` template 指向 `grc_blocks/`（試過，太脆弱）——而是用
  一個 2 行的轉接檔 `gr_plot_capture.py`，放在流程圖旁邊，把 `import
  gr_plot_capture` 導向 `grc_blocks/gr_plot_capture.py`，不管 GRC 產生
  `.py` 時用的是哪個版本的快取 block 定義都能動。如果
  `gr_plot_capture.py`/`gr_plot_sink.py` 以後又搬家，要改的是這個轉接檔，
  不是 `plot_capture.block.yml`。
- **`05a_IQtoOgg_plot.grc` 自己的 `id` 不能帶 `05a` 前綴**（它會變成產生出來
  的 `.py` 裡的 class 名稱——而 Python 識別字不能以數字開頭），所以 GRC
  永遠只會寫出 `IQtoOgg_plot.py`；重新產生後要自己手動改名成
  `05a_IQtoOgg_plot.py`。同一個限制也是為什麼
  `s05b_header_locate_decode.m` 前面要多一個字母 `s`——已經實際驗證過，
  檔名不是合法識別字的 MATLAB script 完全跑不起來（`run('05z_test.m')`
  會直接把 `05z_test` 當成運算式解析而報錯）。
- **`soundfile.write` 對 `.wav` 的預設 subtype 是 16-bit PCM，會靜靜地把
  訊號裁切（clip）到 [-1, 1]**——這些錄音帶有真實、未正規化的振幅（峰值
  超過 ±70），用預設值寫檔會直接把訊號毀掉。一定要傳
  `subtype="FLOAT"`（見 `05c_destuff_interactive.py` 的
  `crop_segment()`），跟來源 `05a` wav 本身的格式一致。
- **不要直接把幾千萬個原始點畫進圖裡。** Step 2 診斷圖的早期版本把完整
  解析度的波形＋z-score 軌跡整個畫進去——存出來的 `.fig` 高達 995MB，而且
  視覺上完全沒用（這個縮放層級下整個糊成一塊實心色塊，看不出任何峰值）。
  已經用 min/max envelope 降採樣修掉（跟
  `01_frame_detection/01a_waveform_power_overview.py` 同一套慣例）：分成
  約 8000 個桶，每個桶同時保留最小值跟最大值讓真正的峰值一定留得下來，
  檔案降到 466KB，資訊量完全一樣。
- **`05a` 沒辦法從外部安全地自動化跑到乾淨結束。** 它的 `Throttle` block
  把速度卡在錄音本身的真實時長，而且不會在檔案讀完時自動結束（只有手動
  關視窗，或 SIGINT 觸發 GRC 產生的 `sig_handler` 才會）——而且在
  Windows 上，`os.kill(pid, SIGTERM)` 實際上呼叫的是
  `TerminateProcess`（直接強制砍掉），完全繞過那個 handler，可能讓 wav
  的 RIFF header 沒有正確收尾。要用互動的方式跑，不要寫腳本在等一段時間
  後強制砍掉。
