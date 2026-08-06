# 02 — zero-run 段落的判決方法：baseline + 判決，標出孤立的「1」

> 這是 [`README.md`](README.md)（公開在 GitHub 上、給英語社群看的版本）的中文對照版，
> 提供給看得懂中文的讀者參考，**不是**要取代公開版本；內容架構刻意對齊
> `01_frame_detection/`、`03_data_segment_processing/`、`04_Beacon/`
> 三個資料夾的 README 寫法（What this is about → Algorithm → Scripts →
> Key findings → How to run），方便交叉比對。

## 這個資料夾在做什麼

`zero-run1`/`zero-run2` 是 payload 裡兩段已知全部都是 `0x00` 填充的段落
（payload byte `[75,158)` 跟 `[206,272)`，協定固定、不受 telemetry 影響）。
理論上這兩段應該平平地貼在接近 -A 的地方，但實際上裡面會出現幾個孤立的
「判成 1」的 symbol——這是通道/ISI 造成訊號漂移之後最難判決的一種 bit（連續
同極性一段時間之後，訊號會慢慢往中線漂，讓一個真正孤立、極性相反的 bit
振幅被壓縮）。

`02_zero_run_baseline.py` 做兩件事：

1. **偵測 frame + 定位 zero-run**：frame 起點偵測用的方法跟
   [`01_frame_detection/`](../01_frame_detection/) 完全一樣（正規化互相關
   找 header 相關性峰值；細節見那個資料夾 README 的「Algorithm」章節）。
   找到每個 frame 的起點之後，用同一套逐 bit destuff 對應表（輸出 bit ↔
   原始 sample index）算出這個 frame 精確的 zero-run1/zero-run2 sample
   範圍——沒有任何 frame 的 sample 位置是寫死的。
2. **比較兩種判決方法**，只套用在 zero-run1/zero-run2 上（header/data/FCS
   不受影響）。

## 演算法（判決方法的部分）

### 符號說明

| 符號 | 意義 |
|---|---|
| $Y[m]$ | `restore_baseline` 的 `y_comp_final` 輸出（原始 sample $m$） |
| $\mathrm{start}$ | 這個 frame 的起始 sample（用跟 `01_frame_detection/` 同一套方法偵測出來） |
| $\mathrm{SPS}$ | 每個 symbol 的取樣點數（=5） |
| $\mathrm{PHASE}$ | 取樣相位（=2，每個 symbol 5 個 sample 裡最接近 symbol 中心的偏移量，用 header 驗證過誤差最小） |
| $\mathrm{idx}(n)$ | zero-run 段落裡，symbol $n$ 中心對應到的原始 sample index |
| $y[n]$ | 那個 symbol 中心的訊號值，$y[n]=Y[\mathrm{idx}(n)]$ |
| $\hat b[n]$ | 判決結果（0 或 1） |
| $d[n]$ | 相鄰 symbol 的差，$d[n] = y[n]-y[n-1]$ |
| $\theta$ | Method 2 的判決閾值 |
| $p$ | 百分位數（50/70/80/90） |

### 0. 取樣：從波形到 $y[n]$

`restore_baseline` 對原始訊號做 decision-directed baseline restoration，
產生 $Y$。對 zero-run 段落內的 symbol $n$（段落內 0-based index），它的取樣點
sample index 是：

$$
\mathrm{idx}(n) = \mathrm{start} + \mathrm{SPS}\cdot n + \mathrm{PHASE}
\qquad\Longrightarrow\qquad
y[n] = Y[\mathrm{idx}(n)]
$$

換句話說，每個 symbol 只用「最接近中心」的單一 sample 點代表，沒有做平均或
內插——底下兩種判決方法都是根據這個單一取樣值 $y[n]$ 來判決的。

**Method 1（固定閾值）**：跟 `01_frame_detection/` 一樣，用固定的 0 當閾值：

$$
\hat b[n] = \begin{cases} 1, & y[n] > 0 \\ 0, & \text{otherwise} \end{cases}
$$

**Method 2（逐點變化量）**：想法是 zero-run 段落大部分是平的，所以一個真正
孤立的 1，可能在「變化量」上比在「絕對值」上更明顯：

$$
\hat b[n] = \begin{cases} 1, & |d[n]| > \theta \\ 0, & \text{otherwise} \end{cases}
$$

$\theta$ 沒有先驗值，所以候選閾值是從 zero-run 段落裡 $|d[n]|$ 分佈的百分位數
$p\in\{50,70,80,90\}$ 取出來的（不是隨便挑的），分別大致對應
50%/30%/20%/10% 的判成 1 比率。

## 腳本

| 腳本 | 輸出 |
|---|---|
| `02_zero_run_baseline.py` | 自動偵測錄音裡的每個 frame，每個 frame 各存一張圖：`Figure/02_frame1.png`、`Figure/02_frame2.png`、`Figure/02_frame3.png`……（依照 sample 位置排序編號） |

## 主要發現

**Method 1（固定閾值）明顯勝出**：三個 frame 的 zero-run 段落裡，Method 1
的判成 1 比率只有 0.8-2.5%；Method 2 在每一個測試過的閾值下都是 9-53%——也就是
說，對這個訊號而言，**絕對值比局部變化率更值得信任**。這一開始看起來有點
反直覺（局部差分通常對緩慢漂移比較穩健），但原因是 zero-run 段落本身的絕對值
波動其實沒有那麼大，反而是相鄰 symbol 之間一直存在的小雜訊，被差分放大成了
誤判。這個結論在三個 frame 上都一致——不是綁定在單一封包上的巧合。

輸出的圖會疊出兩種方法各自判成「1」的位置，並標出每個判成 1 的 symbol 的
全域 index 跟它在段落內的相對位置，方便直接對照原始波形用肉眼檢查。

## 怎麼跑

```bash
pip install numpy matplotlib soundfile
python 02_zero_run_baseline.py
```

需要上一層目錄的 `../scionx/`（`audio_io.py` / `baseline.py`）跟
`../_style.py`，腳本會自動找到，不用另外裝。跑完之後，每個偵測到的 frame 各
存一張新的 PNG 到這個資料夾的 `Figure/` 子資料夾，並在終端機印出每個 frame
偵測到的 zero-run 範圍、兩種方法在每個閾值下的判成 1 比率，以及 Method 1
判成「1」的確切位置。
