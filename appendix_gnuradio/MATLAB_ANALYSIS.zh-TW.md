# MATLAB 取點分析說明

這份文件說明 `plot_pickpoints_seg017.m` 和 `tune_decision_points.m` 兩支 MATLAB 腳本：它們在算什麼、對應到什麼方法、公式是什麼，以及過程中用到的專有名詞。

寫這兩支的動機：Python 那邊（`symbol_sync_sweep.py`、`plot_pickpoints_comparison.py`）產出的是**靜態 PNG**，看到可疑的地方沒辦法放大。MATLAB 這兩支刻意寫得很單純——**不跑 Symbol Sync、不做 frame 偵測、不做 destuff**，該有的常數直接寫在檔案開頭，本體就只有 `audioread` + `plot`，所以可以隨便改、隨便縮放。

---

## 專有名詞速查

| 名詞 | 意思 |
|---|---|
| **符元 (symbol)** | 一次調變傳送的最小單位。這裡是 GFSK，一個符元帶 1 個 bit |
| **sps (samples per symbol)** | 一個符元佔幾個取樣點。本專案 = 48000 Hz ÷ 9600 baud = **5.0** |
| **符元時脈回復 (symbol timing recovery)** | 接收端不知道發射端的符元邊界在哪，要從訊號自己推出來。`symbol_sync_ff` 做的就是這件事 |
| **TED (timing error detector)** | 時脈誤差偵測器。回復迴路的核心：判斷「現在取樣點是太早還太晚」。M&M = Mueller & Muller，是其中一種演算法 |
| **loop_bw** | 迴路頻寬。大 = 反應快但抖；小 = 穩但追不上變化 |
| **damping** | 阻尼係數。控制迴路收斂時會不會過衝震盪 |
| **max_dev** | 允許迴路偏離名目 sps 的最大量（單位：取樣點） |
| **ted_gain** | 告訴迴路「TED 的預期斜率」，用來把迴路係數正規化 |
| **判決器 (slicer)** | 把類比值轉成 0/1。`binary_slicer_fb` 是**看正負號**，所以判決門檻 $\theta = 0$ |
| **決策時刻 (decision instant)** | 每個符元實際被取樣、送進判決器的那個時間點 |
| **取樣相位 (sampling phase)** | 決策時刻在符元週期內的位置。理想是落在「眼睛」正中央 |
| **眼圖 (eye diagram)** | 把每個符元週期疊在一起畫。中間張開的空洞叫「眼睛」，越開代表 0/1 越好分辨 |
| **決策餘裕 (decision margin)** | 取樣值離判決門檻多遠。越遠 = 要翻轉這個 bit 需要越多雜訊 = 越穩 |
| **HDLC flag** | `0x7E` = `01111110`，標記 frame 的頭尾 |
| **bit stuffing** | HDLC 規定：資料中每出現連續 5 個 `1`，就強制插入一個 `0`，避免資料被誤認成 flag。所以**正常資料裡連續 1 絕不會超過 5 個** |
| **destuff** | 接收端把那些插入的 `0` 拿掉，還原原始資料 |
| **FCS / CRC-16/X.25** | frame 末尾的 2 bytes 檢查碼。對得上才算解碼成功 |
| **polarity norm / inv** | FM 解調後 0/1 有可能整段相反，所以兩種極性都要試 |

---

## 共通的關鍵細節

### 索引從 0 還是從 1

Python 算出來的取樣位置是 **0-based**（第一個樣本是 0），MATLAB 陣列是 **1-based**（第一個是 `y(1)`）。所以腳本裡定義：

```matlab
t_idx = (0:numel(y)-1)';   % 0-based 位置，跟 Python 對齊
```

然後查值一律透過 `interp1(t_idx, y, pos)`，不要直接寫 `y(pos)`，否則整體會差一個樣本。

### 為什麼用內插而不是四捨五入

取樣位置是**小數**（例如 918610 + 5.0×n 在調整相位後會有小數）。如果直接取整數，等於自己引入了最多半個樣本的相位誤差——而相位誤差正是我們要量的東西，這樣就白量了。所以用線性內插：

$$y(p) \approx y(\lfloor p \rfloor) + (p - \lfloor p \rfloor)\bigl(y(\lfloor p \rfloor + 1) - y(\lfloor p \rfloor)\bigr)$$

對應 `interp1(..., 'linear')`。

---

## `plot_pickpoints_seg017.m`：兩種取點方式的對照

回答的問題是：**「01/03 那套固定網格」和「Symbol Sync」分別在哪裡取樣？**

### 兩條網格的公式

固定網格（`01`/`03` 的做法，直接照 sample clock 數）：

$$\mathrm{idx}_{\text{fixed}}(n) = s_0 + \mathrm{SPS}\cdot n$$

Symbol Sync 的平均網格：

$$\mathrm{idx}_{\text{ss}}(n) = s_0 + \overline{\mathrm{sps}}\cdot n, \qquad \overline{\mathrm{sps}} = \frac{N_{\text{in}}}{N_{\text{out}}}$$

其中 $N_{\text{in}}$ 是輸入樣本總數、$N_{\text{out}}$ 是迴路吐出的符元總數。兩條共用同一個起點 $s_0$。

對應程式碼：

```matlab
avg_sps      = numel(y) / N_OUT_BITS;
sample_start = BIT_START * avg_sps;
idx_fixed    = sample_start + SPS_NOM * n;
idx_symsync  = sample_start + avg_sps * n;
drift        = idx_symsync - idx_fixed;
```

兩者的累積差距是線性的：

$$d(n) = \mathrm{idx}_{\text{ss}}(n) - \mathrm{idx}_{\text{fixed}}(n) = (\overline{\mathrm{sps}} - \mathrm{SPS})\cdot n$$

### ⚠️ 這支腳本畫出來的 drift 是假的

`avg_sps` 是**整個 30 秒檔案的平均**，但這個檔案裡大約 29 秒是雜訊，迴路在雜訊區是自由亂跑的。所以這個數字描述的是「迴路在雜訊裡的行為」，**不是 frame 的真實符元率**。

seg017 算出來是 4.9234，看起來像 1.5% 的時脈偏移、能畫出「累積漂移 168 個樣本」的驚人曲線——**這個解讀已經被推翻**（見下一節實測）。真實符元率就是 5.000，frame 內部**根本沒有漂移**。

這支腳本現在的用途是「看取樣點落在哪」，**不要**把兩條網格的分離當成真實時脈漂移的證據。

---

## `tune_decision_points.m`：決策點好不好、能不能更好

回答的問題是：**現在的決策時刻是不是取在眼睛正中央？可以更好嗎？**

### 決策餘裕的定義

判決器是看正負號，門檻 $\theta = 0$。所以每個決策時刻的「安全程度」就是它離 0 多遠。把整個 frame 平均起來，得到相位 $\varphi$ 的餘裕函數：

$$M(\varphi) = \frac{1}{N}\sum_{n=0}^{N-1} \bigl| y(s_0 + \varphi + \mathrm{SPS}\cdot n) - \theta \bigr|$$

最佳相位就是讓它最大的那個：

$$\varphi^{*} = \arg\max_{\varphi} M(\varphi)$$

對應程式碼：

```matlab
ph_grid = linspace(-SPS_USE/2, SPS_USE/2, 201);   % 掃一整個符元週期
for i = 1:numel(ph_grid)
    margin(i) = mean(abs(sample_at(picks + ph_grid(i)) - THRESH), 'omitnan');
end
[best_margin, i_best] = max(margin);
```

掃描範圍取 $\pm\mathrm{SPS}/2$ 正好涵蓋一個完整符元週期（再多就開始重複了）。`'omitnan'` 是因為邊界外的內插會回傳 `NaN`，要跳過。

**注意這是穩健度的代理指標，不是誤碼率。** 餘裕變大代表「決策沒那麼勉強」，不代表某個特定 bit 真的改變了。

### 眼圖怎麼建

把每個符元的決策時刻當原點，往前後各取一個符元寬度：

$$E(k, \tau) = y(p_k + \tau), \qquad \tau \in [-\mathrm{SPS},\ +\mathrm{SPS}]$$

$k$ 是第幾個符元，全部疊起來畫就是眼圖。

```matlab
tau = linspace(-SPS_USE, SPS_USE, n_eye);
eye = sample_at(picks + tau);     % picks 是 Nx1、tau 是 1xM -> 結果 NxM
```

這裡用到 MATLAB 的 **隱式擴展 (implicit expansion)**：一個直向量加一個橫向量，會自動展開成矩陣。等同於 `bsxfun(@plus, picks, tau)`，R2016b 以後可以直接寫 `+`。

### 畫 2201 條線的技巧

眼圖有 2201 條軌跡。如果用 `for` 迴圈呼叫 2201 次 `plot()` 會非常慢，而且圖例會爆掉。標準做法是**用 NaN 當分隔符，把全部軌跡串成單一個 line object**——MATLAB 遇到 `NaN` 會斷線，剛好達到「分段」的效果：

```matlab
Xe = [repmat(tau, N_SYMBOLS, 1), nan(N_SYMBOLS,1)]';
Ye = [eye,                       nan(N_SYMBOLS,1)]';
plot(Xe(:), Ye(:), 'Color', [0 0.447 0.741 0.06]);
```

顏色用 4 個元素（RGB + alpha），透明度 0.06 讓密集的軌跡疊出濃淡，看得出哪裡是主流路徑。

### 眼圖張開程度

用最佳與最差相位的餘裕比值來量化：

$$R = \frac{\max_{\varphi} M(\varphi)}{\min_{\varphi} M(\varphi)}$$

$R \approx 1$ 代表曲線是平的 = **根本沒有眼圖**（取樣點在符元週期裡糊掉了）；$R$ 明顯大於 1 才代表有可辨識的眼睛。

> 註：`tune_decision_points.m` 本身印的是「目前餘裕 / 最佳餘裕 / 改善百分比」。上面這個 $R$ 值是另外用 Python 掃多個 sps 算的（下一節的表），腳本裡沒有直接印。

---

## 實測結論

### sps 確實是 5.000

固定 seg017 已確認的 frame，用不同 sps 折眼圖比較張開程度：

| `sps` | 最佳餘裕 | 張開程度 $R$ |
|---|---|---|
| **5.00000** | 4.5887 | **1.094** |
| 4.98 | 4.4589 | 1.022 |
| 4.96 | 4.4306 | 1.010 |
| 4.92336（迴路全檔平均） | 4.4194 | 1.008 |
| 4.90 | 4.4027 | 1.003 |

**只有 5.000 撐得出眼圖**，其他值全部接近全平。所以：

- `sps = 5.0`（= 48000/9600）成立
- `01`/`03` 的固定網格在 frame 內部**沒有**相對漂移
- 它們解不開這些錄音，原因**不是**時脈漂移

### 眼圖只是勉強張開

$R = 1.094$ 不算大。而且最佳相位跟目前錨點差 **−2.0 個樣本**，改過去餘裕多 6.4%。

但這個 −2 很可能是**錨點本身算錯**造成的——`FRAME_START = 918610` 是用那個已被推翻的 `avg_sps = 4.92336` 推出來的。要真正釐清，得拿已知的 2202 個 on-wire bit 去跟波形做互相關，把 frame 起點精確定位。

---

## 怎麼調整

### `plot_pickpoints_seg017.m`

檔案開頭的常數；換別的 frame 就改這些：

| 常數 | 意義 |
|---|---|
| `WAV` | 音檔路徑 |
| `N_OUT_BITS` | 該檔案 Symbol Sync 吐出的符元總數 |
| `BIT_START` / `BIT_END` | frame 在符元流裡的起訖索引（含） |
| `SPS_NOM` | 固定網格的間距，預設 5.0 |

這些數字由 Python 端印出來：

```bash
python plot_pickpoints_comparison.py Output/<檔名>.wav \
    --ted MM --loop-bw 0.045 --damping 0.7 --max-dev 1.5 --whole
```

輸出的 `bit range=[..., ...]` 和 `out of N total symbols` 就是要填的值。

### `tune_decision_points.m`

兩個旋鈕：

| 旋鈕 | 用途 |
|---|---|
| `SPS_USE` | 符元間距。預設 5.0；填 4.92336 可以重現「沒有眼圖」的對照 |
| `PHASE_ADJ` | 把所有決策時刻平移幾個樣本。填 `-2.0` 可以看最佳相位的樣子 |

改完直接重跑（F5），圖會重畫並印出新的餘裕數字。

---

## 常見問題

**Q: 圖上藍點橘點看起來完全重疊，是不是壞了？**

在整段尺度下本來就會這樣（一萬多個樣本擠在一起）。要放大才看得出差異——這正是寫 MATLAB 版的原因。用工具列的放大鏡框選局部即可（腳本結尾已經 `zoom on`）。

**Q: 為什麼兩個 panel 的縮放不連動？**

刻意不連動。上面 panel 的 x 軸是「樣本索引」，下面是「符元索引」，單位不同，連動只會互相干擾。（`plot_pickpoints_seg017.m` 裡有註解說明。）

**Q: 眼圖畫出來很糊，是腳本問題嗎？**

不一定。這段訊號本身的眼圖就只有 $R = 1.094$，本來就不漂亮。先把 `SPS_USE` 設 5.0 確認是最清楚的版本，如果還是很糊，那就是訊號品質的極限，不是腳本的問題。

**Q: MATLAB 說 `audioread` 找不到檔案？**

腳本裡用的是絕對路徑（跟 `view_segment.m` 一致）。如果搬過資料夾，改檔案開頭的 `WAV` 常數。

---

## 相關檔案

| 檔案 | 說明 |
|---|---|
| `symbol_sync_sweep.py` | 掃 Symbol Sync 參數、離線評分找出可解碼的組合 |
| `plot_pickpoints_comparison.py` | Python 版取點圖（靜態 PNG，`--whole` 看整段） |
| `plot_pickpoints_seg017.m` | 本文說明的第一支：兩種取點對照，可縮放 |
| `tune_decision_points.m` | 本文說明的第二支：眼圖 + 餘裕曲線 |
| `README.md` | 完整的 seg017 解碼記錄、參數工作區間、以及被撤回的說法 |
