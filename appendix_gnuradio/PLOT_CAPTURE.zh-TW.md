# Plot Capture 使用說明

`plot_capture` 是一個自訂的 GRC block，作用是：**在 flowgraph 裡接一條線，就能在跑完之後自動存一張 PNG**，不用再另外開 File Sink 存 `.bin`，也不用再叫一支獨立的 Python 腳本讀檔畫圖。

畫布上看到的是一個方塊、一個輸入埠；內部其實是一個 `Vector Sink`（把樣本收進記憶體），跑完之後由一個 Python Snippet 呼叫它的 `.plot()` 方法畫圖存檔。`Capture Samples` 這個上限是在 **Python 這邊用切片做的**，不是靠 GNU Radio 的 `Head` block 硬性中斷資料流——原因見下方「常見問題」，這是實測抓出來的一個重要地雷。

## 一次性前置設定

`plot_capture` 是專案自帶的「local block」（放在 `grc_blocks/` 資料夾），GRC 預設不會去找那個資料夾，需要設定一次 `local_blocks_path`：

```python
from gnuradio import gr
p = gr.prefs()
p.set_string('grc', 'local_blocks_path', r'D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\grc_blocks')
p.save()
```

用 `radioconda` 的 Python 執行這段（例如在 radioconda Prompt 裡 `python` 進互動模式貼上執行）。**不要直接手動編輯 config 檔**——GNU Radio 實際讀的設定檔在 `C:\Users\<你的帳號>\.gnuradio\config.conf`，跟 `AppData\Roaming\.gnuradio\config.conf`（長得很像但沒作用）是兩個不同檔案，容易改錯。用上面這段程式讓 GNU Radio 自己決定要寫哪裡最保險。

設定完 **重新開啟 GNU Radio Companion**（如果原本就開著，舊視窗的 block 清單是啟動當下建立的，不會自動重新掃描）。之後 `plot_capture` 就會出現在 block tree 的 `[Appendix GNURadio]` 分類下，跟內建 block 一樣可以直接拖拉使用。

如果搬動了 `appendix_gnuradio` 資料夾位置，要重新執行一次上面的設定、指到新路徑。

## 怎麼用

### 1. 接線

```
你想觀察的訊號  ->  Plot Capture
```

只需要一條線，接在你想「拍照存檔」的那個點（例如 Throttle 之後、Quadrature Demod 之後）。

### 2. 屬性面板

| 參數 | 說明 |
|---|---|
| `Input Type` | 接的線是什麼型態：`complex` / `float` / `int` / `short` / `byte`，**要跟接線的訊號型態一致**，不然接線會顯示紅色（型態不符）|
| `Sample Rate` | 這個點的取樣率，畫圖的時間軸/頻率軸要用，通常直接填 `samp_rate` 變數 |
| `Capture Samples` | 要抓幾個樣本（決定畫多長一段）。**這是在畫圖時才截斷的上限，不會讓資料流提早停止**——block 會一直收樣本收到 flowgraph 停止為止，只是最後畫圖只取前面這麼多個。如果你讓 flowgraph 跑很久才關掉，記憶體用量會跟著跑的時間增加，不是固定在這個數字 |
| `Skip (settling)` | 跳過開頭幾個樣本，用來避開濾波器/鎖相環的暫態，預設 0 |
| `Plot Mode` | 要畫哪些圖，見下方「Plot Mode 可用值」 |
| `Samples/Symbol (eye)` | 只有 `eye`（眼圖）面板要用，填一個 symbol 有幾個取樣點 |
| `FFT Size (psd/spec)` | `psd`、`spec` 面板的 FFT 點數，預設 1024 |
| `Output Dir` | 輸出資料夾（相對於執行時的工作目錄），預設 `Figure` |
| `Filename Prefix` | 檔名開頭，方便分辨是哪個觀察點存的圖 |
| `Title` | 圖表標題，留空的話會用 `Filename Prefix` |
| `Also Save .npy` | 開啟後除了 PNG，也會把抓到的原始樣本存成 `.npy`（含 dtype/shape，之後可以直接用 numpy 讀回來重畫，不用再猜格式） |

### 3. Plot Mode 可用值

`Plot Mode` 可以填逗號分隔的組合，或用下面三個簡寫：

| 值 | 展開成 |
|---|---|
| `all` | `time,psd,spec` |
| `rf` | `psd,spec`（射頻/IQ 訊號常用，看頻譜跟瀑布圖） |
| `af` | `time,psd,eye`（解調後的音訊常用） |

也可以自己組合，例如 `time,psd,eye,hist`。單一面板意義：

| 面板 | 內容 |
|---|---|
| `time` | 時域波形。複數輸入會分開畫 I / Q 兩條線。樣本數超過約 8000 點時會自動做 min/max 包絡線壓縮，畫面不會卡、也不會漏掉任何尖峰 |
| `psd` | 平均週期圖（Hann window、50% overlap） |
| `spec` | 頻譜圖（瀑布圖）。色階自動夾在 5～99.8 百分位，避免雜訊佔滿整個色階、看不到真正的載波 |
| `hist` | 振幅（或複數輸入的幅值）直方圖 |
| `iq` | I 對 Q 的二維直方圖，只有複數輸入能用 |
| `eye` | 眼圖，需要設定 `Samples/Symbol` |

### 4. 加 Snippet 觸發畫圖

flowgraph 裡再拉一個 **Python Snippet** block，`Section` 選 `Main - After Stop`，內容依 block 名稱寫（block 名稱看左上角屬性欄位的 `ID`，或畫布上方塊下面顯示的名字）：

```python
self.plotcap_iq.plot()
self.plotcap_af.plot()
```

> **注意：這裡的 flowgraph 物件是 `self`，不是 `tb`**（即使 GRC 產生的函式簽名參數名稱看起來像跟 `tb` 有關，實際上 GRC 產生的是 `def snipfcn_xxx(self)`，一定要用 `self`）。

跑完之後（QT GUI 模式下就是你把視窗關掉的那一刻），PNG 就會自動出現在 `Output Dir` 指定的資料夾裡，檔名固定帶時間戳，不會互相覆蓋。

如果想臨時換一種畫法但不想改屬性面板，可以在 `.plot()` 裡直接覆蓋參數：

```python
self.plotcap_iq.plot(mode='iq')       # 這次只想看星座圖
self.plotcap_af.plot(save_npy=True)   # 這次順便存原始樣本
```

### 5. 週期性連續存圖：每 N 秒存一張，直到訊號播完

如果不想只在最後存一張，而是**訊號一直播、每隔固定秒數就存一張圖，直到播完為止**，改用 `start_periodic_plot()` / `stop_periodic_plot()`，要拉**兩個** Snippet：

```python
# Section 選 Main - After Start
self.plotcap_iq.start_periodic_plot(30)   # 每 30 秒存一張

# Section 選 Main - After Stop（如果你提早關視窗，這裡會把剩下不滿 30 秒的
# 那一小段也存成圖；如果是訊號自然播完，start_periodic_plot 自己就會偵測到
# 沒有新資料進來，自動存最後一段、自動停止，不用等你關視窗）
self.plotcap_iq.stop_periodic_plot(flush_partial=True)
```

檔名會自動加上段落編號：`iq_seg000_spec_....png`、`iq_seg001_spec_....png`……以此類推。

原理：這是用 `PyQt5` 的 `QTimer` 定時去讀已經在成長中的 `Vector Sink`，跟畫面上那些即時波形/瀑布圖用的機制一樣，**不是**寫一個新的 streaming block，所以完全不會踩到「streaming Python block 在這台環境會 segfault」那個雷。判斷「訊號播完了」的方式，是連續好幾次檢查都發現資料量沒有再增加（代表上游的檔案來源已經讀到結尾），就自動存最後一段殘餘資料、自動停止計時器。

`IQtoOgg_plot.grc` 目前 `plotcap_iq` 就是用這個模式，每 30 秒存一張。

**`Capture Samples`（`nsamples`）跟 `start_periodic_plot()` 的 `interval_sec` 對不上也沒關係，兩者完全獨立**，已經實測驗證過：週期性擷取每一段的長度完全由 `interval_sec` 決定，因為 `start_periodic_plot` 是直接讀 `Vector Sink` 裡原始、持續成長中的資料，不會經過 `nsamples` 那個截斷邏輯（那個截斷只有一次性的 `.plot()` 會用到）。`Capture Samples` 唯一還有意義的地方，是如果你在同一個 block 上**同時**又呼叫了 `.plot()`，那張圖只會秀出前 `nsamples` 的內容，跟週期性擷取實際跑到哪裡無關；另外它也還是 `Vector Sink` 的記憶體預留提示值，設多少都不會出錯，只是預留空間大小不同而已。

### 6. 範例

`IQtoOgg_plot.grc` 已經是一個可以直接參考的完整範例：

- `plotcap_iq`：接在 Throttle 之後，`type=complex`、`mode=rf` —— 看濾波前的原始 IQ 頻譜跟瀑布圖
- `plotcap_af`：接在 Quadrature Demod 之後，`type=float`、`mode=time,psd` —— 看解調後的音訊波形跟頻譜

用 GNU Radio Companion 打開這個檔案，對照著看每個屬性欄位怎麼填最直覺。

## 常見問題

**開啟 `.grc` 看到 `plot_capture` 是 missing block？**
GRC 沒找到 `local_blocks_path`，多半是因為：(1) 還沒做過上面的一次性設定，或 (2) 設定完但 GRC 視窗是設定之前就開著的舊 session。存檔後完全關閉 GNU Radio Companion 再重開即可。

**為什麼不是接一個 sink block 就好，還要多一個 Snippet？**
因為畫圖的動作（呼叫 matplotlib、存檔）必須在 flowgraph **停止之後**才能做，而 GRC 沒有「flowgraph 停止後對某個 block 呼叫某個方法」這種內建機制，Snippet 是唯一能插入這種「跑完之後執行」程式碼的地方。`plot_capture` 已經把工作量壓到最低——你只需要在 Snippet 裡寫一行 `self.<名稱>.plot()`。

**能不能直接把它做成一個「即時 sink」，資料流過去就自動存檔，不用 Snippet？**
技術上會用到 `gr.sync_block`（本身要能處理串流資料的 block），但**這台 radioconda 安裝下任何帶 stream port 的 Python block 都會 segfault**（已經實測確認過，跟這個 block 本身無關，是環境問題）。所以才改用 `gr.hier_block2`（內部完全是 C++ block）+ Snippet 這個組合來繞開。

**`Input Type` 選錯會怎樣？**
接線會出現紅色警示（型態不符），沒辦法執行；照著你接的訊號實際型態填就好（例如接在 IQ 訊號上要選 `complex`，接在解調後的音訊上要選 `float`）。

**跑到某個固定秒數（剛好等於 `Capture Samples ÷ Sample Rate`）之後畫面就整個卡住、不管是波形圖、瀑布圖都不再更新？**
這是 `plot_capture` 早期版本的一個真實 bug，已經修掉了（如果你的檔案是舊版產生的，重新拉一個新的 `Plot Capture` block 或更新 `gr_plot_capture.py` 即可）。

原因：早期版本內部是 `Head`（抓滿指定樣本數就停止）接 `Vector Sink`。`Head` 一旦抓滿，就永遠不會再去讀它上游共用的那塊緩衝區——但 GNU Radio 的緩衝區只要還有「沒被讀走」的資料卡在裡面，就沒辦法回收空間給新資料寫入。結果就是：只要 `Plot Capture` 接的那個點，同時還有別的東西在用（例如同一個 Throttle 輸出同時接了瀑布圖、頻譜圖），`Head` 抓滿的那一刻，**上游會被硬生生卡死**，連帶讓所有共用同一個訊號源的顯示全部凍結，且不會自己恢復。這已經用實測重現並確認過因果關係。

現在的版本拿掉了 `Head`，`Vector Sink` 會一直收資料收到你手動停止 flowgraph 為止，`Capture Samples` 只是最後畫圖時取前面幾個樣本的上限，不會讓資料流卡住。代價是：如果你讓 flowgraph 跑很久才關（例如接即時訊號源開著好幾個小時），記憶體會持續成長，不會被 `Capture Samples` 這個數字自動限制住——但對這個專案的實際用法（有限長度的錄音檔，看個幾秒到幾分鐘就會關掉）來說，這個取捨是對的：比起讓所有畫面整個當機，可控制的記憶體成長是小很多的代價。
