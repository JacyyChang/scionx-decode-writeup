# 01 — Frame 偵測：自動找出封包起點 + 切出結構

> 這是 [`README.md`](README.md)（公開在 GitHub 上、給英語社群看的版本）的中文對照版，
> 提供給看得懂中文的讀者參考，**不是**要取代公開版本；內容架構刻意對齊
> `02_zero_run_baseline/`、`03_data_segment_processing/`、`04_Beacon/`
> 三個資料夾的 README 寫法（What this is about → Algorithm → Scripts →
> Key findings → How to run），方便交叉比對。

## 這個資料夾在做什麼

原始錄音是一段連續的音訊，裡面藏著幾個 frame。我們得先自動找出「訊號裡哪裡有
封包」，再把每個封包切成 flags（`0x7E`）/callsign address/data/zero-run
（填充）/FCS（CRC）這幾個段落。

`01_frame_detection.py` 做兩件事：

1. **偵測**：用已知的 header + callsign address（4 個 flag + 14 bytes 的
   address = 144 bits，協定固定內容，不受 telemetry 影響）做出一個 NRZ 樣板，
   拿去跟 `restore_baseline` 的 `y_comp_final` 輸出做正規化互相關，找出相關係數
   遠高於雜訊本底的位置——這些就是 frame 起點。不需要事先知道有幾個 frame，也
   沒有寫死任何 sample 位置。
2. **切段與標記**：對每個偵測到的 frame，逐 bit 做 HDLC destuff，同時
   **記錄每個輸出 bit 對應到原始錄音的哪個 sample**，再對照
   `../REFERENCE_FRAME.md` 裡已知的欄位邊界，把整個結構精確換算回 sample
   index，畫在圖上上色，並在每個邊界標上「這是 payload byte N」。

## 演算法

### 符號說明

| 符號 | 意義 |
|---|---|
| $b[i]\in\{0,1\}$ | header 的原始 bit 序列（第 $i$ 個 bit，$i=0,\dots,143$） |
| $s[i]\in\{-1,+1\}$ | $b[i]$ 換算出來的 NRZ 極性 |
| $\mathrm{SPS}$ | 每個 symbol 的取樣點數（=5） |
| $t[k]$ | 升取樣後的比對樣板，$k=0,\dots,L-1$ |
| $L$ | 樣板長度（samples），$L=144\times\mathrm{SPS}=720$ |
| $y[n]$ | `restore_baseline` 的 `y_comp_final` 輸出（sample $n$） |
| $y[n{:}n{+}L]$ | 從位置 $n$ 開始、長度 $L$ 的訊號視窗 |
| $R[n]$ | 位置 $n$ 的正規化互相關係數，範圍 $[-1,1]$ |
| $\sigma_R$ | $R$ 在整段訊號上的標準差（雜訊本底的尺度） |
| $z[n]$ | $R[n]$ 的 z-score |
| $Z_{\text{th}}$ | frame 偵測用的 z-score 門檻（預設 12） |
| $\mathcal{C}$ | 通過門檻的候選位置集合 |
| $S$ | 最終保留下來的 frame 起點集合 |
| $G_{\min}$ | 兩個保留下來的起點之間的最小 sample 間距（預設 100000） |

### 1. 建立比對樣板

把 header 的固定內容（4 個 `0x7E` flag + 14 bytes 的 callsign address =
32+112 = 144 bits，協定固定、不受 telemetry 影響）換成 NRZ 極性：

$$
s[i] = 2b[i] - 1 ,\qquad i = 0,\dots,143
$$

再用「每個 symbol 重複 $\mathrm{SPS}$ 次」的方式升取樣（重複，不是內插）：

$$
t[k] = s\!\left(\left\lfloor \frac{k}{\mathrm{SPS}} \right\rfloor\right),
\qquad k = 0,\dots,L-1,\quad L = 144 \times \mathrm{SPS} = 720
$$

### 2. 正規化互相關

逐點掃描訊號 $y$，在每個位置 $n$ 算樣板 $t$ 跟訊號視窗 $y[n{:}n{+}L]$ 的
正規化互相關係數：

$$
R[n] = \frac{\displaystyle\sum_{k=0}^{L-1} t[k]\,y[n+k]}
             {\lVert t \rVert \, \lVert y[n{:}n{+}L] \rVert},
\qquad
\lVert t \rVert = \sqrt{\sum_{k=0}^{L-1} t[k]^2},\quad
\lVert y[n{:}n{+}L] \rVert = \sqrt{\sum_{k=0}^{L-1} y[n+k]^2}
$$

$R[n]$ 大概落在 $[-1,1]$ 之間：視窗跟樣板波形越像，$R[n]$ 就越接近 1。實作上，
分子（內積）用 `np.correlate(y, t, mode="valid")` 一次算出所有 $n$；分母的視窗
norm 則用 $y^2$ 的前綴和 $\mathrm{csum}[m]=\sum_{j<m}y[j]^2$ 來算滑動視窗能量
$\lVert y[n{:}n{+}L]\rVert^2 = \mathrm{csum}[n{+}L]-\mathrm{csum}[n]$——兩邊都
不需要逐點迴圈。

### 3. 用 z-score 篩選相關性峰值

$$
z[n] = \frac{R[n]}{\sigma_R}, \qquad \sigma_R = \mathrm{std}(R)
$$

真正的 frame 起點在 $z$ 上會明顯突出（在 `cut_first3.ogg` 上量到大約
$z\approx12\sim13$），雜訊本底則大多維持在 $z<5$——兩者分得很開。把通過門檻的
位置當作候選集合：

$$
\mathcal{C} = \{\, n \mid z[n] > Z_{\text{th}} \,\}
$$

### 4. 去重複、排序、編號

把 $\mathcal{C}$ 依照 $z[n]$ 由大到小排序，依序貪婪地加進起點集合 $S$
（一開始是空集合）：

$$
n \in \mathcal{C}\ (\text{依 } z[n] \text{ 由大到小排序}):\quad
n \to S \iff \min_{s \in S} |n - s| > G_{\min}
$$

$G_{\min}$ 遠小於實際的 frame 間距（約 561000 samples），可以避免同一個峰值
附近的樣本被算成好幾個不同的候選點。最後把 $S$ 依照 sample 位置由小到大排序，
編成 frame#1、frame#2、frame#3……

## 腳本

| 腳本 | 輸出 |
|---|---|
| `01_frame_detection.py` | 自動偵測錄音裡的每個 frame，每個 frame 各存一張圖：`Figure/01_frame1.png`、`Figure/01_frame2.png`、`Figure/01_frame3.png`……（依照 sample 位置排序編號） |
| `01a_waveform_power_overview.py` | 在跑上面那支真正的偵測器之前，先對一份新錄音快速看一眼：畫出原始波形 + 逐區塊 RMS 功率包絡，用肉眼抓候選 burst 大概在哪裡。不做 baseline restoration，也不做 frame 偵測，所以就算是好幾分鐘的錄音也很快。`Figure/01a_overview_<音檔名稱>[_<start>-<end>s].png` |
| `01b_weak_signal_candidate_scan.py` | 針對訊號太弱、`01_frame_detection.py` 的同調相關性抓不到的 frame，做兩階段候選掃描：第一階段找「平均功率明顯低於區域中位數」的窗格（窗格長度對齊一個 frame 的長度，是一種匹配濾波，不是天真的逐區塊門檻——原因見下面說明）；第二階段對每個存活下來的候選點，拿它的過零點間隔結構跟旁邊一段長度相同的雜訊窗格比較評分。結果印在終端機的候選排行表，並存前幾名的圖：`Figure/01b_candidate<名次>_<音檔名稱>.png` |

已經在 `Data/cut_first3.ogg`（已知含有 3 個 frame）上驗證過：偵測結果剛好落在
三個已知起點 235719 / 796789 / 1357824 上，z-score 都在 12-13 之間，遠高於
雜訊本底（z<5），沒有誤判。

### `01a`：長錄音可以用 `--start`/`--end` 縮小範圍

```bash
python 01a_waveform_power_overview.py path/to/long.ogg                    # 整份，會先印出總長度
python 01a_waveform_power_overview.py path/to/long.ogg --start 30 --end 90   # 只看 30~90 秒
```

`--start`/`--end` 是秒數，直接傳給 `scionx.audio_io.read_audio` 的
`start_sec`/`end_sec`（soundfile 會直接 seek、只解碼那段範圍，不是整份讀完
再切片——縮小範圍真的會省掉解碼工作，不只是畫圖工作）。波形面板不管範圍多長，
都會 min/max 分桶壓縮到 `--max-points` 欄（預設 6000），所以就算是整份錄音也
畫得很快；短脈衝不會因為壓縮而消失，因為每一欄同時保留最小值跟最大值，不是
每個桶只留一個樣本。

**在一份真實長錄音上發現的注意事項**
（`satnogs_14674078_2026-08-03T07-50-10.ogg`，606 秒）：GFSK 本身是定振幅
調變，所以不管有沒有真的封包，RMS 功率面板整段幾乎都是平的——振幅本身沒辦法
像 OOK（開關鍵控）訊號那樣，一眼分出「這裡有訊號」跟「只是雜訊/待機載波」。
短暫的凹陷（不是隆起的平台）通常代表接收機掉訊，不是封包。這支腳本比較適合
當作定位工具（這份錄音多長、有沒有明顯異常），不能取代
`01_frame_detection.py` 那套看 header 實際位元結構的相關性偵測器。

### `01b`：抓住太弱、同調相關性偵測器抓不到的 frame

`01_frame_detection.py` 的 144-symbol 相關性需要所有 symbol 同調對齊才能
累積增益；訊噪比一低，就算把視窗剪得緊貼著真封包，這個增益也會整個垮到雜訊
本底（已在 `satnogs_14674078_2026-08-03T07-50-10.ogg` 上驗證：整份 606 秒
錄音裡最大 z 只有 5.1，跟「這裡沒有 frame」沒兩樣）。`01b` 改成找獨立、成本
較低的證據，不靠同調相位對齊：

```bash
python 01b_weak_signal_candidate_scan.py path/to/long.ogg
python 01b_weak_signal_candidate_scan.py path/to/long.ogg --dip-ratio 0.6 --top 15
```

第一階段對整份錄音算逐區塊 RMS（不做 baseline restoration——這是快速、粗略的
一輪），滑動一個對齊 frame 長度的窗格（`--window`，預設對齊
`cut_first3.ogg` 自己的 0.400 秒），找「平均功率低於區域中位數乘上
`--dip-ratio`」的窗格（用區塊化、而非全域的中位數，可以容忍緩慢的 AGC/仰角
變化造成的漂移）。這裡必須用窗格平均，不能要求「連續一段裡每個區塊都個別低於
門檻」：訊號本身的振幅起伏，就算在真封包內部，也會在任何固定比值
上下擺動——用天真的逐區塊門檻去測 `cut_first3.ogg` 三個已知真封包，一個都抓
不到，所以才改用這種匹配濾波的做法（已驗證能在 11 毫秒誤差內找回全部 3 個
已知起點，才敢拿去用在新資料上；這個現象原本歸因於 AFSK 的雙音調，參見
`01b_weak_signal_candidate_scan.py` 的 `BLOCK_MS` 註解——實測結果仍然成立，
但 GFSK 底下真正的機制還沒重新推導）。第二階段把每個第一階段存活下來的候選點
緊緊裁切出來，對這一小段真的做 baseline restoration，檢查過零點間隔是不是像
真正 GFSK symbol 那樣聚集在 SPS 的倍數上，並拿候選點前面一段長度相同的雜訊窗格當
對照組評分（每份錄音自我校準，因為絕對的比例門檻沒辦法套用到不同訊噪比的
錄音上）。這支腳本本身不會解出 frame——它只是幫你縮小範圍，決定接下來要把
`01_frame_detection.py` 自己的相關性搜尋（門檻可以順便調低）或
`04_Beacon/` 的工具指向哪裡。

## 主要發現

- **Frame 偵測相當準確**：z-score 門檻抓到的 3 個起點跟已知的
  235719/796789/1357824 完全吻合，沒有漏抓也沒有誤判。
- **從圖上看，結構切段（header/data/zero-run/FCS 邊界）也相當準確**——疊圖
  上色跟原始波形對得起來。但這只代表「位置切對了」，不代表「切出來的 bit
  內容是對的」：data 段（真正的 telemetry 內容）的 bit error rate 還是很高
  （15-43%），這是 `03_data_segment_processing/` 要處理的問題。
- **`01b` 的兩階段掃描找到一個同調偵測器完全抓不到、但看起來很有機會的弱訊號
  候選點**：在 `satnogs_14674078_2026-08-03T07-50-10.ogg`（606 秒，z-score
  偵測器整份都找不到任何東西，最大 z 只有 5.1）上，`01b` 在 `--dip-ratio`
  從 0.45 到 0.75 這麼大的範圍內，一致地只回傳**一個**候選點，位置 t=183.52
  秒，depth-ratio 0.45——剛好落在 `cut_first3.ogg` 三個真封包量出來的已知
  範圍（0.44-0.52）內。目前還沒有經過 CRC 或成功解碼確認，但已經是個值得把
  後續 pipeline 對準過去的強力線索。

## 怎麼跑

```bash
pip install numpy matplotlib soundfile
python 01_frame_detection.py
```

需要上一層目錄的 `../scionx/`（`audio_io.py` / `baseline.py` / `hdlc.py`）跟
`../_style.py`，腳本會自動找到，不用另外裝。跑完之後，每個偵測到的 frame 各
存一張新的 PNG 到這個資料夾的 `Figure/` 子資料夾，並在終端機印出偵測到的
frame 清單（起始 sample + z-score），以及每個 frame 的「payload byte index
→ sample index」對照表。
