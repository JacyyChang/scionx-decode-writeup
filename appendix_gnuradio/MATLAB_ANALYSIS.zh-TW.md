# MATLAB 分析工具說明

這份文件說明 `appendix_gnuradio/` 底下三支現行 MATLAB 分析工具：`eye_fixed_grid.m`、`power_dip_locate.m`、`header_correlate_locate.m`。它們在算什麼、對應到什麼方法、公式是什麼，以及過程中用到的專有名詞。

> **2026-08-27 更新**：本文件先前涵蓋的 `tune_decision_points.m` 和 `plot_pickpoints_seg017.m` 已被 `eye_fixed_grid.m` 取代——前者的「margin」指標有嚴重瑕疵（見下方「被推翻的舊指標」），後者畫的「Symbol Sync 平均網格」也建立在已被推翻的 `avg_sps` 解讀上。兩支舊檔案還留著，但不要引用它們算出來的數字。

寫這些工具的動機：Python 那邊（`symbol_sync_sweep.py`、`plot_pickpoints_comparison.py`）產出的是**靜態 PNG**，看到可疑的地方沒辦法放大，也沒辦法直接用 MATLAB 生態圈（Signal Processing Toolbox 等）快速檢驗。這幾支刻意寫得很單純——**不跑 GNU Radio Symbol Sync、不做複雜的框架**，該有的常數直接寫在檔案開頭，本體就是 `audioread` + 訊號處理 + `plot`，所以可以隨便改、隨便縮放。

---

## 專有名詞速查

| 名詞 | 意思 |
|---|---|
| **符元 (symbol)** | 一次調變傳送的最小單位。這裡是 GFSK，一個符元帶 1 個 bit |
| **sps (samples per symbol)** | 一個符元佔幾個取樣點。本專案 = 48000 Hz ÷ 9600 baud = **5.0** |
| **固定網格 (fixed grid)** | 假設 sps 恆定、不做任何回授修正的取樣方式：`idx(n) = s0 + sps*n`。跟 Symbol Sync 的差別在於它完全不追蹤時脈變化 |
| **判決器 (slicer)** | 把類比值轉成 0/1。`binary_slicer_fb` 是**看正負號**，所以判決門檻 $\theta = 0$ |
| **決策時刻 (decision instant)** | 每個符元實際被取樣、送進判決器的那個時間點 |
| **取樣相位 (sampling phase)** | 決策時刻在符元週期內的位置，範圍 $[0, \mathrm{sps})$ |
| **眼圖 (eye diagram)** | 把每個符元週期疊在一起畫。中間張開的空洞叫「眼睛」，越開代表 0/1 越好分辨 |
| **眼圖開口 (eye opening)** | 本文件採用的正確指標：「最弱的 1」減「最強的 0」。正值代表所有決策都在門檻正確的一側 |
| **RMS 功率凹陷 (power dip)** | 真實 GFSK 訊號出現時，FM 解調的接收機雜訊會被壓低（FM quieting），所以真訊號是功率**凹陷**、不是凸起 |
| **匹配濾波器 (matched filter)** | 對功率曲線做「跟一個 frame 等寬的移動平均」，把單一取樣點的雜訊平滑掉，讓真正的凹陷區間更好辨識 |
| **正規化互相關 (normalized cross-correlation)** | 拿一個已知樣板（波形片段）跟訊號逐點比對相似度，正規化到跟訊號振幅無關 |
| **z-score** | 互相關係數除以它自己的標準差。真訊號的相關峰值會遠高於雜訊底噪（z 值差幾倍），藉此分辨「真的對上」還是「碰巧像」|
| **HDLC flag** | `0x7E` = `01111110`，標記 frame 的頭尾 |
| **bit stuffing** | HDLC 規定：資料中每出現連續 5 個 `1`，就強制插入一個 `0`，避免資料被誤認成 flag。所以**正常資料裡連續 1 絕不會超過 5 個** |
| **DC 偏移 / DC 階躍** | 訊號的直流成分（平均值）偏離 0，或在某個時間點突然改變。會讓「原始點積相關」失準，見 `header_correlate_locate.m` 那節 |
| **Pearson 相關** | 先把兩個序列都減掉各自的平均值再算相關，天生不受 DC 偏移影響 |
| **polarity norm / inv** | FM 解調後 0/1 有可能整段相反，所以兩種極性都要試 |

---

## 共通的關鍵細節

### 索引從 0 還是從 1

Python 算出來的取樣位置是 **0-based**（第一個樣本是 0），MATLAB 陣列是 **1-based**（第一個是 `y(1)`）。三支腳本都用 `t_idx = (0:numel(y)-1)'` 這種寫法讓內部運算維持 0-based 語意，跟 Python 端印出來的數字直接對得上，只有在真正要索引 `y(...)` 的地方才會 +1。

### 為什麼取樣位置要內插，不能四捨五入

取樣位置常常是小數（例如相位 0.2、sps 4.9234 之類）。如果直接取整數，等於自己引入了最多半個樣本的相位誤差——而相位誤差正是我們要量的東西，這樣就白量了。所以一律用線性內插：

$$y(p) \approx y(\lfloor p \rfloor) + (p - \lfloor p \rfloor)\bigl(y(\lfloor p \rfloor + 1) - y(\lfloor p \rfloor)\bigr)$$

對應 `interp1(..., 'linear')`。

---

## 被推翻的舊指標（`tune_decision_points.m`，勿再使用）

舊版工具算的是：

$$M_{\text{舊}}(\varphi) = \frac{1}{N}\sum_{n=0}^{N-1} \bigl| y(s_0 + \varphi + \mathrm{SPS}\cdot n) - \theta \bigr|$$

也就是**全部符元的平均距離**。問題是 seg017 的 274-byte frame 裡有 1882 個符元是 zero-run padding（全部集中在 −4 附近），只有 320 個是真正變化的 header/telemetry bit。平均值被 padding 徹底主導，導致不管相位怎麼調，$M_{\text{舊}}$ 幾乎不動——算出來的「張開程度」是 1.094（幾乎全平），讓人誤以為眼圖「勉強張開」。

**實際上眼圖張得很開**（見下面 `eye_fixed_grid.m` 的正確結果）。教訓：**平均距離不是眼圖開口**，眼圖開口關心的是「最危險的那個決策」，不是「所有決策的平均」。

---

## `eye_fixed_grid.m`：正確的眼圖開口指標

回答的問題是：**在固定 sps 網格（完全不跑 Symbol Sync）下，這個 frame 的眼睛開多大？在哪個相位最好？**

### 正確的開口定義

判決器看正負號，門檻 $\theta = 0$。把某個相位 $\varphi$ 下的所有決策值分成兩群：

$$\mathcal{H}(\varphi) = \{\, y(p) : y(p) > 0 \,\}, \qquad \mathcal{L}(\varphi) = \{\, y(p) : y(p) \le 0 \,\}, \qquad p = s_0+\varphi+\mathrm{SPS}\cdot n$$

**眼圖開口**定義成：

$$\mathrm{opening}(\varphi) = \min\bigl(\mathcal{H}(\varphi)\bigr) - \max\bigl(\mathcal{L}(\varphi)\bigr)$$

對應程式碼：

```matlab
hi = v(v >  THRESH);
lo = v(v <= THRESH);
opening(i) = min(hi) - max(lo);
```

這個指標只看「最弱的 1」跟「最強的 0」——也就是最容易被雜訊翻轉的那兩個決策——完全不受 padding 符元數量拖累。$\mathrm{opening}>0$ 代表**這個相位下沒有任何一個判決會翻轉**（若雜訊不再變大）。

最佳相位：

$$\varphi^{*} = \arg\max_{\varphi} \mathrm{opening}(\varphi)$$

掃描範圍取 $[0, \mathrm{SPS})$ 正好涵蓋一個完整符元週期。

### 實測結果（seg017 確認解碼）

| sps | 開口 |
|---|---|
| **5.00000** | **+5.04** |
| 4.98 ~ 4.90 | ≤ 0（完全沒有眼圖）|

只有 5.000 撐得出眼圖，證實 `sps=5.0` 是對的，而且 frame 內部沒有時脈漂移。

### 眼圖怎麼建

把每個符元的決策時刻當原點，往前後各取一個符元寬度：

$$E(k, \tau) = y(p_k + \tau), \qquad \tau \in [-\mathrm{SPS},\ +\mathrm{SPS}]$$

```matlab
tau = linspace(-SPS, SPS, n_eye);
eye = sample_at(picks + tau);     % picks 是 Nx1、tau 是 1xM -> 隱式擴展成 NxM
```

### 畫兩千多條線的技巧

用 **NaN 分隔符**把全部軌跡串成單一個 line object，避免迴圈呼叫 `plot()` 兩千多次：

```matlab
Xe = [repmat(tau, N_SYMBOLS, 1), nan(N_SYMBOLS,1)]';
Ye = [eye,                       nan(N_SYMBOLS,1)]';
plot(Xe(:), Ye(:), 'Color', [0 0.447 0.741 0.05]);
```

顏色第 4 個元素是透明度，讓密集軌跡疊出濃淡層次。

### header 範圍標記

`HEADER_START`/`HEADER_LEN` 兩個常數（來自 `header_correlate_locate.m` 的輸出）會在右下角波形圖上畫出黃色網底，標出「4 個 flag + 14-byte 位址」樣板實際比對到的位置。設 `HEADER_START = NaN` 可以關掉這個標記。

### 怎麼調整

| 常數 | 意義 |
|---|---|
| `WAV` | 音檔路徑 |
| `FRAME_START` | frame 內容起點（樣本索引，已含相位）|
| `N_SYMBOLS` | frame 涵蓋的符元數 |
| `SPS` | 固定網格間距，預設 5.0 |
| `PHASE` | 在 `FRAME_START` 上再疊加的相位微調，預設 0 |
| `POLARITY` | +1 或 −1，處理 FM 解調可能整段反相的情況 |
| `DECODE_LABEL` | **手動填寫**，不是自動判斷——正開口不代表 HDLC flag 對得上、CRC 一定過，所以這個標籤要照實際解碼結果填 |
| `HEADER_START` / `HEADER_LEN` | header 樣板的位置與長度（720 = 144 bits × SPS），來自 `header_correlate_locate.m` |

---

## `power_dip_locate.m`：粗定位（功率凹陷）

回答的問題是：**在一段 30 秒的錄音裡，封包大概在哪裡？**

### 方法

1. 20ms 區塊算 RMS，轉 dB（跟 `view_segment.m` 同慣例）
2. 用一個「跟真實 frame 等寬」的**匹配濾波器**（移動平均）平滑功率曲線——單一區塊的凹陷很容易被雜訊誤判，用等寬窗口平均可以把「一整段 frame 造成的持續凹陷」跟「單點雜訊」分開
3. 取平滑後曲線的**全域最小值**當候選位置

```matlab
mf_len = round(FRAME_DUR_S * 1000 / WINDOW_MS);   % 換算成區塊數
mf = movmean(rms_db, mf_len);
[~, i_dip] = min(mf);
```

### ⚠️ 系統性偏差：抓到的是凹陷「中心」，不是內容「起點」

匹配濾波器的最小值落在整個凹陷區間（flag + 內容 + 前後 padding）的**中心**，不是內容起點。用兩個已知答案驗證：兩邊都差了「約半個 frame 寬度」（+5667 / +5715 個 sample），修正這個系統性偏移後，誤差降到 frame 寬度的 1.5%~1.9%：

```matlab
half_frame_samples = round(FRAME_DUR_S * fs / 2);
cand_sample = dip_sample - half_frame_samples;
```

**這個精度（frame 寬度的 1~2%，換算約 150~350 個 sample）只夠當粗定位**——不足以判斷某個固定相位到底解不解得開，這也是為什麼還需要下一支工具做精定位。seg010 的案例證實了這個限制：粗定位的候選位置後來被 header 互相關證明差了超過一個 frame 寬度。

---

## `header_correlate_locate.m`：精定位（header 互相關）

回答的問題是：**封包精確從哪一個 sample 開始？**

移植自 `01_frame_detection/01_frame_detection.py` 的 `detect_frame_starts()`：用「4 個 flag（`0x7E`×4）+ 14-byte 目的/來源位址」建一個 NRZ 樣板（這段內容在同一顆衛星的每個 frame 裡都固定不變，不受 telemetry 內容影響），依 sps 放大，跟波形逐點算正規化互相關。

### 跟 Python 原版的一個刻意差異：只讓樣板去均值

Python 原版對「已經做過 baseline restoration（去 DC）」的訊號算原始點積相關。這支 MATLAB 版改成**只把樣板減掉平均值**（Pearson 相關的簡化版）：

$$R(n) = \frac{\sum_k \bigl(y(n+k) - \theta_y\bigr)\bigl(t(k) - \bar t\bigr)}{\lVert t - \bar t\rVert \cdot \sqrt{\sum_k (y(n+k)-\theta_y)^2}}$$

因為樣板 $t-\bar t$ 已經是零均值，$\sum_k (t(k)-\bar t) = 0$，所以視窗自己的均值 $\theta_y$ 在分子的交叉項裡會被消掉——不需要額外算視窗的滑動平均，只要讓樣板去均值就自動獲得對 DC 偏移不敏感的效果。程式碼：

```matlab
tmpl_c = template - mean(template);        % 只做這一次，重複使用
num  = conv(yw, flipud(tmpl_c(:)), 'valid');           % 分子：不需要減視窗均值
s1   = conv(yw,    ones(L,1), 'valid');                % 視窗滑動和（給分母用）
s2   = conv(yw.^2, ones(L,1), 'valid');                % 視窗平方滑動和
win_var_L = max(s2 - (s1.^2)/L, 0);                    % L * 視窗變異數
R = num ./ (tmpl_norm * sqrt(win_var_L) + 1e-12);
z = R / std(R);
```

會特別處理這件事，是因為 seg010 的波形中間有明顯的 DC 階躍（見 `eye_fixed_grid.m` 的波形圖）——原始點積相關對這種偏移敏感，Pearson 版天生免疫。

### z-score 判斷真假

$z = R / \mathrm{std}(R)$，真訊號的相關峰值遠高於雜訊底噪（本專案的經驗值：雜訊底噪 z<5，真訊號 z 落在 8~14 之間）。

### 校準 flags→content 偏移量

互相關的峰值位置對應「樣板的第一個樣本」，也就是**leading flags 的起點**，不是 frame 內容的起點。用兩個已知答案校準這個固定偏移量：

$$\text{offset} = \text{已知內容起點} - \text{header 峰值位置}$$

實測 seg017 = 161 samples（32.2 符元）、seg002 = 160 samples（**精確 32.0 符元**）——跟理論值「4 個 flag = 32 bits」完全吻合，是很紮實的驗證。校準出的平均偏移量（32.1 符元）可以直接套用到還沒解碼的段落上，把 header 峰值換算成內容起點的估計值。

### 這支工具抓到的重大發現

seg010 的 header 相關峰值 z=13.71（**比兩個已確認解碼的段落都強**），但位置跟 `power_dip_locate.m` 抓到的粗定位候選相差 **7332 個 sample**——超過一個 frame 寬度。這證實了粗定位法的精度上限，也解釋了為什麼一開始在錯誤位置量到的眼圖是純雜訊的單峰分布：**根本沒有看對地方**。換到 header 相關指出的正確位置後，眼圖才出現真正的（雖然重疊嚴重的）雙峰結構。

---

## 常見問題

**Q: 為什麼 `eye_fixed_grid.m` 的 `DECODE_LABEL` 要手動填，不能自動判斷？**

因為「這個相位下眼圖開口 > 0」不等於「HDLC flag 位置對得上、CRC 通過」。開口是必要條件，不是充分條件——有可能眼睛開了但 flag 定位錯誤導致解不出正確的 byte 邊界。所以工具刻意不用開口數字自動下結論，逼你去核對真正的解碼結果（用 `symbol_sync_sweep.py` 或手動跑 `offline_deframe`/CRC）。

**Q: `power_dip_locate.m` 抓到的位置跟 `header_correlate_locate.m` 差很多，該信哪個？**

信 header 互相關的。功率凹陷法的物理原理（FM quieting）沒錯，但匹配濾波器的解析度受限於「frame 寬度」這個窗口大小，天生只能抓到大概位置；header 互相關是逐 sample 比對已知的固定 bit pattern，解析度高得多。實務上应该：先用功率凹陷法框出大概範圍（省得在整段 30 秒裡搜尋 header），再用 header 互相關在那個範圍內精確定位。

**Q: 為什麼 `header_correlate_locate.m` 要限制搜尋範圍（`SEARCH_MARGIN`），不乾脆搜整個檔案？**

技術上可以搜整個檔案，只是比較慢，而且如果訊號裡有其他碰巧像 header 的雜訊尖峰，範圍越大誤判機率越高。用功率凹陷法先框出大概範圍，再用 header 互相關精確定位，是刻意的兩階段設計。

**Q: MATLAB 說 `audioread` 找不到檔案？**

三支腳本都用絕對路徑。如果搬過資料夾，改檔案開頭的 `WAV`（或 `segments` 結構裡每個 `file` 欄位）。

---

## 相關檔案

| 檔案 | 說明 |
|---|---|
| `symbol_sync_sweep.py` | 掃 Symbol Sync 參數、離線評分找出可解碼的組合（本文件的三支 MATLAB 工具問世後，發現很多情況下根本不需要這支——先試固定網格）|
| `plot_pickpoints_comparison.py` | Python 版取點圖，⚠️ 其漂移曲線建立在已被推翻的 `avg_sps` 解讀上，勿照字面採信 |
| `eye_fixed_grid.m` | 本文說明的第一支：固定網格眼圖，正確的開口指標 |
| `power_dip_locate.m` | 本文說明的第二支：功率凹陷粗定位 |
| `header_correlate_locate.m` | 本文說明的第三支：header 互相關精定位 |
| `plot_pickpoints_seg017.m` / `tune_decision_points.m` | ⚠️ 已被取代，見文件開頭 |
| `README.md` | 完整的 seg017/seg002 解碼記錄、seg010/seg007 的定位與 SNR 現況、以及所有被撤回的說法 |
