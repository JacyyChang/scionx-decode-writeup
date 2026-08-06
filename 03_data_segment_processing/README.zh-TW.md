# 03 — 處理 data 段落：header 校正閾值 + 兩種呈現方式

> 這是 [`README.md`](README.md)（公開在 GitHub 上、給英語社群看的版本）的中文對照版，
> 提供給看得懂中文的讀者參考，**不是**要取代公開版本；內容架構刻意對齊
> `01_frame_detection/`、`02_zero_run_baseline/`、`04_Beacon/`
> 三個資料夾的 README 寫法（What this is about → Algorithm → Scripts →
> Key findings → How to run），方便交叉比對。

## 這個資料夾在做什麼

`data1`/`data2` 是 payload 裡真正的 telemetry 內容（每個 frame 都不一樣，
沒有已知正確答案）。`header` + callsign address（144 bits，協定固定內容，
是唯一有已知正確答案的段落）被拿來量測 bit=1/bit=0 訊號分佈的不對稱性：
bit=1 的平均值接近 +A，但 bit=0 的平均值明顯偏離 -A。這個資料夾把 header
校正出來的固定偏移閾值套用到 data1/data2 上，用兩支腳本呈現：

| 腳本 | 內容 | 輸出 |
|---|---|---|
| `03a_data_asymmetry_check.py` | 顯示不對稱性本身：逐點上色 + 每組各自的局部移動平均，看這個不對稱性會不會隨段落內的位置緩慢漂移 | `Figure/03a_frame1.png`、`03a_frame2.png`、`03a_frame3.png` |
| `03b_data_fixed_offset_threshold.py` | 把校正出來的固定偏移閾值套用到 data1/data2 上，疊出判決線，並圈出所有貼近這條線的 symbol——**不管在線的下面還是上面**。第二個面板的範圍延伸到 data2 之後的 zero-run2 跟 FCS，並標出這些邊界 | `Figure/03b_frame1.png`、`03b_frame2.png`、`03b_frame3.png` |
| `03c_symbol_sync_timing.py` | ⚠️ 需要 GNU Radio（這個 repo 裡唯一的例外）。用 GNU Radio 真正的 `digital.symbol_sync_ff` 取代固定速率/固定相位取樣，在 frame#1 上掃過 TED 類型 × loop bandwidth × 前端濾波器（共 125 組設定），依序用 CRC 是否通過 > header bit error 數 > 貼近判決線的數量來評分 | `Figure/03c_frame1.png` |

兩支腳本的 frame 偵測都用跟
[`01_frame_detection/`](../01_frame_detection/) 一樣的方法（正規化互相關
找 header 相關性峰值），每偵測到一個 frame 就輸出一張圖（依照 sample 位置
排序編號）。

★ **Bypass method**：不管是定位 data1/data2 的 sample 範圍，還是計算閾值，
兩支腳本都是直接對**原始 y** 用固定速率取樣、destuff，完全不經過
`restore_baseline`——已驗證這樣 header 對齊最乾淨。`restore_baseline` 只用在
frame 起點偵測那一步（需要 `y_comp_final` 才能得到乾淨的相關性峰值）；真正的
閾值校正跟 data 段落分析，用的都只有原始 y。

## 演算法

### 符號說明

| 符號 | 意義 |
|---|---|
| $Y[m]$ | 原始訊號（sample $m$，`audio_io.read_audio` 讀進來的值，沒有經過 baseline 補償） |
| $\mathrm{start}$ | 這個 frame 的起始 sample（用跟 `01_frame_detection/` 同一套方法偵測出來） |
| $\mathrm{SPS},\mathrm{PHASE}$ | 跟 `01_frame_detection/` 一樣：每個 symbol 的取樣點數（=5）、取樣相位（=2） |
| $t[i]\in\{0,1\}$ | header+address 裡第 $i$ 個 bit 的已知真值，$i=0,\dots,143$ |
| $v_1, v_0$ | header 裡真值是 1 / 0 的 symbol 中心訊號值集合 |
| $\theta$ | 候選閾值 |
| $\theta^*$ | 校正出來的最佳固定偏移閾值 |
| $y[n]$ | data1/data2 裡 symbol $n$ 的中心訊號值 |
| $\hat b[n]$ | data 段落的判決結果（0 或 1；★ 這是閾值判決，不是已知真值） |
| $A$ | 振幅估計值，$A=\mathrm{median}(\lvert Y\rvert)$（整份錄音） |
| $m$ | 「接近判決線」的半寬，$m = \text{MARGIN\_FRAC}\cdot A$（預設 $\text{MARGIN\_FRAC}=0.10$，即 $\pm 10\%A$） |

### 1. Frame 偵測

跟 `01_frame_detection/` 完全一樣的正規化互相關方法——細節見那個資料夾
README 的「Algorithm」章節，這裡不重複。

### 2. 從 header 校正固定偏移閾值

拿 header+address 144 個已知 bit 的 symbol 中心值，依照真值分成兩組：

$$
v_1 = \{\, Y[\mathrm{start}+\mathrm{SPS}\cdot i+\mathrm{PHASE}] \mid t[i]=1 \,\},
\qquad
v_0 = \{\, Y[\mathrm{start}+\mathrm{SPS}\cdot i+\mathrm{PHASE}] \mid t[i]=0 \,\}
$$

在 $[\min(v_0\cup v_1),\ \max(v_0\cup v_1)]$ 之間掃過候選閾值 $\theta$，取
錯誤數最少的那個當作最佳固定偏移閾值：

$$
\theta^* = \arg\min_{\theta}\ \Big(\ \bigl|\{x\in v_1 \mid x \le \theta\}\bigr|\ +\ \bigl|\{x\in v_0 \mid x > \theta\}\bigr|\ \Big)
$$

這個閾值是從**這個 frame 自己的** header 重新校正出來的，不是沿用別的
frame 算出來的值。

### 3. 定位 data1/data2（bypass method）

跳過開頭 4 個 flag 之後，對原始 $Y$ 用固定速率取樣（$\mathrm{SPS}$ 跟
$\mathrm{PHASE}$ 不變），用閾值 0 切成 0/1，做 HDLC destuff，過程中記錄每個
輸出 bit 原本的 sample index。接著對照參考封包（`../REFERENCE_FRAME.md`）
已知的欄位邊界（data1 = payload byte `[14,75)`、data2 = payload byte
`[158,206)`），換算回 sample 範圍，得到這個 frame 自己精確的
`data1`/`data2` sample 範圍。

`03b` 把 data2 的範圍再往後延伸一段，涵蓋 zero-run2（`[206,272)`）跟 FCS
（`[272,274)`，附加在 272-byte payload 後面的 2-byte CRC-16/X.25）——所以它
的第二個面板會把 data2+zero-run2+FCS 畫成一段連續的範圍，並用垂直虛線標出
zero-run2/FCS 的邊界。`03a` 不受影響，還是只畫 data2 本身。

### 4a. 顯示不對稱性（`03a`）

用 $\theta^*$ 把 data1/data2 裡每個 symbol 分成「判成 1」/「判成 0」兩組
（★ 這是閾值判決，不是已知真值）：

$$
\hat b[n] = \begin{cases} 1, & y[n] > \theta^* \\ 0, & \text{otherwise} \end{cases}
$$

畫出兩種視角：(1) 逐點散佈圖，依照 $\hat b[n]$ 上色；(2) 每組各自的局部移動
平均（窗格是用「組內的點數」來算的），看兩組的平均值會不會隨段落內位置緩慢
漂移。

### 4b. 套用判決線，標出低信心的 symbol（`03b`）

直接把同一個 $\theta^*$ 當判決線畫在原始波形上，並保留舊閾值（=0）的線當
對照。接著圈出所有跟 $\theta^*$ 的距離落在一個對稱半寬內的 symbol，
**不管在哪一側**：

$$
\bigl|\,y[n]-\theta^*\,\bigr| \le m
$$

這跟「$\theta=0$ 和 $\theta=\theta^*$ 之間判決結果改變的 symbol」是不同（而且
更大）的集合：那種比較法只會抓到落在兩個閾值之間那條窄帶裡的 symbol，會漏掉
那些剛好落在 $\theta^*$ **上方**的低信心 symbol——它們從來沒有跟舊閾值的判決
結果不一致過，但一樣貼近目前的判決線。改成圈
$\lvert y[n]-\theta^*\rvert \le m$ 就能對稱地抓到兩邊，呼應主 README 裡
「Possible future updates」提到的想法：把最貼近判決線的 bit 找出來，當作
最容易出錯、最可疑的候選。這裡沒有算 bit error rate——data 段落的內容每個
frame 都不一樣，沒有可靠的已知真值可以比對。

第二個面板延伸出去的範圍（data2+zero-run2+FCS）三段都套用同一個沒有變動過的
$\theta^*$——也就是說，它顯示的是同一個 header 校正出來的閾值，套在全零填充
跟 CRC 上會發生什麼事，不只是 `03a` 原本聚焦的 telemetry 部分。zero-run2
用灰色底標出來（它是預期全零的段落，不是這裡真正要看的對象），FCS 則是
青色——這樣可以一眼看出 CRC 的 bit 本身有沒有低信心 symbol，不會被 zero-run2
數量大得多的候選點淹沒。終端機輸出也會單獨印出只限定在 FCS 這段範圍內的
貼近判決線數量。

## 主要發現

- **不對稱性在三個 frame 上都重現**，雖然校正出來的閾值不一樣（frame#1
  +0.151、frame#2 +0.200、frame#3 +0.075）——不對稱性這件事本身是穩定的，
  但確切大小因 frame 而異，不是單一的全域常數。
- **`03a` 的局部移動平均顯示這個不對稱性不是固定偏移量，而是會隨段落內位置
  緩慢漂移**（「隱藏曲線」）：判成 0 那組的平均值在段落中間附近會漂得比較
  接近 0，靠近兩端又漂回去，而判成 1 那組相對穩定。這代表單一固定偏移閾值
  只能校正平均值，沒辦法完全消除這個跟位置有關的分量。
- **`03b` 顯示新舊閾值的判決差異小但不是零**：三個 frame 裡，data1/data2
  大約 2-7% 的 symbol 換閾值後判決結果會變；新閾值會把「判成 0」那組的平均值
  推得更遠離 -A（見 `03a` 的輸出），但沒有 CRC 或其他獨立驗證能確認這樣做
  是不是真的比較準。
- **對稱的貼近判決線區間（$\theta^*$ 左右各 10%A）標出的是大小相近、但不
  完全相同的一群**：三個 frame 裡，每個段落大約 1.6-6.7% 的 data1/data2
  symbol——其中還包括一些剛好落在 $\theta^*$ 上方、從來沒在新舊閾值比較裡
  出現過的 symbol。這些正是主 README 裡「對照一個真正解出來的 frame」這個
  方向的天然起點，因為不管哪個閾值才是對的，它們本來就是低信心的。
- **把第二個面板延伸到 zero-run2+FCS，貼近判決線的比例大概翻倍**（從單看
  data2 的 ~2-7%，變成 data2+zero-run2+FCS 一起看的 ~10-14%，三個 frame
  都一樣）——新增的低信心 symbol 大部分落在 zero-run2 裡，跟
  `02_zero_run_baseline/` 的發現一致：全零填充也沒有被乾淨地解出來（那邊
  說的「孤立的 1」，其實就是同一群低信心族群，只是從另一個角度看而已）。
- **FCS 本身看起來很乾淨**：16 個 CRC bit 裡，落在貼近判決線區間內的分別是
  0/16（frame#2）、2/16（frame#1）、2/16（frame#3）——跟 data1/data2 差不多
  的比例，沒有特別差。CRC bit 的低信心數量沒有特別突出，這跟「CRC 本身特別
  難切」這個解釋 0 frame 過 CRC 的假設是矛盾的。
- **真正的 symbol timing recovery（03c）改善了 header BER，但沒有修好
  CRC**：把固定速率/固定相位取樣換成 GNU Radio 真正的 `symbol_sync`
  （最佳設定：Gardner TED、loop_bw=0.08、0.75 倍 symbol rate 的低通前端），
  frame#1 的 header bit error 數從目前固定相位方法的 5/144 降到 2/144，
  眼圖也明顯更乾淨。但即使是最佳設定，CRC 還是過不了——單靠 timing
  recovery 還不是完整的答案。

## 怎麼跑

```bash
pip install numpy matplotlib soundfile
python 03a_data_asymmetry_check.py
python 03b_data_fixed_offset_threshold.py
```

需要上一層目錄的 `../scionx/`（`audio_io.py` / `baseline.py`）跟
`../_style.py`，腳本會自動找到，不用另外裝。跑完之後，每個偵測到的 frame 各
存一張新的 PNG 到這個資料夾的 `Figure/` 子資料夾，並在終端機印出每個 frame
校正出來的閾值、header 錯誤數，以及 data1/data2 判成 1/判成 0 的統計數字。

`03c_symbol_sync_timing.py` 是唯一的例外：需要一個裝了 GNU Radio 的
Python（確認 `radioconda` 裝的可以跑），而且只處理 frame#1（完整跑 3 個
frame × 125 組設定，是 GNU Radio 每次執行的 block-scheduling 額外開銷會
開始有影響的規模）：

```bash
path/to/radioconda/python.exe 03c_symbol_sync_timing.py
```
