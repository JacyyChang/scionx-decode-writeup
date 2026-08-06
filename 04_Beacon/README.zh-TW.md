# 04 — Beacon 欄位對照：把一個 frame 的遙測內容解到實際欄位格式上

> 這是 [`README.md`](README.md)（公開在 GitHub 上、給英語社群看的版本）的中文對照版，
> 只給你自己參考用，**不是**要取代公開版本。內容架構刻意對齊
> `01_frame_detection/`、`02_zero_run_baseline/`、`03_data_segment_processing/`
> 三個資料夾的 README 寫法（What this is about → Algorithm → Scripts →
> Key findings → How to run），方便交叉比對。

## 這個資料夾在做什麼

`01`/`02`/`03`都是停在訊號層級（sample、symbol、判決出來的 bit），從來沒有回答
「這些 bit 到底代表什麼意思」。這個資料夾不一樣：這裡放的是使用者提供的
beacon 遙測欄位格式定義，以及兩支腳本——一支用來檢查 frame 偵測門檻，一支
把某個 frame 的 Info 欄位（payload byte `[16,272)`，256 bytes / 2048 bits，
見 `../REFERENCE_FRAME.md`）對照這個格式解出來。

| 檔案 | 內容 |
|---|---|
| `SCIONX_TLMnew.xlsx` | 欄位格式定義：每一列是一個遙測欄位（`Subsystem`、`ItemName`、`DataType`、`BitLen`、`OffsetBit`、`Endian`、`LongDescription`），共 205 個欄位，緊密排列，`OffsetBit` 從 0 開始累加 = Info 欄位的第一個 bit |
| `SCIONX_enums.json` | enum 對照表（`enums`）、單位/scale 換算（`transforms`）、以及用 regex 把欄位名稱對應到查表規則的 `nameRules`，涵蓋部分欄位 |
| `scionx_destuff_demo.xlsx` | 手工做的獨立教學範例（33 個合成 bit，不依賴任何腳本或錄音）：RawBits 分頁用 ExcludeThisBit/FlipThisBit 下拉選單餵給 Destuffed 分頁，自動重新對齊、對照已知真值評分。驗證了這套互動式 destuff 機制可行，`04d_destuff_interactive.py` 再把它套到真實的 ~2200-bit 原始序列上 |
| `Output/` | 產生出來的 Excel 檔（不會自動重跑更新——要重新產生就重跑對應的腳本） |
| `Figure/` | 產生出來的 `04a_zscore_<音檔名稱>.png` 圖 |

## 演算法

### 符號說明

| 符號 | 意義 |
|---|---|
| $Y[m]$ | 原始訊號（sample $m$），跟 `03` 的符號定義一致 |
| $y_c[m]$ | `restore_baseline` 的 `y_comp_final` 輸出（sample $m$） |
| $\mathrm{start}$ | 這個 frame 的起始 sample（跟 `01_frame_detection/` 同一套方法偵測出來的） |
| $\mathrm{SPS},\mathrm{PHASE}$ | 每個 symbol 的取樣點數（=5）、取樣相位（=2） |
| $Z_{\text{th}}$ | frame 偵測用的 z-score 門檻（預設 12，定義同 `01` ——**但這不是偵測器的固定屬性**，見下面「換一份新錄音時的流程」） |
| $\theta^*$ | 這個 frame 用 header 校正出來的固定偏移閾值（跟 `03` 的 $\theta^*$ 是同一個東西） |
| $A$ | 振幅估計值，$A=\mathrm{median}(\lvert Y\rvert)$ |
| $m$ | 「接近判決線」的半寬，$m=\text{MARGIN\_FRAC}\cdot A$，$\text{MARGIN\_FRAC}=0.10$ |
| $\mathrm{OffsetBit},\mathrm{BitLen}$ | 某個 beacon 欄位在 Info 欄位裡的 bit 位置/寬度，來自 `SCIONX_TLMnew.xlsx` |

### 1. Frame 偵測（跟 `01` 同一套方法，但門檻不能直接沿用）

Frame 起點的找法跟 `01_frame_detection/`的演算法章節完全一樣（用已知的
header+flags 樣板對 $y_c$ 做正規化互相關，再用 $z[n] > Z_{\text{th}}$ 篩選）。
差別在這裡：$Z_{\text{th}}=12.0$ 這個值**只用 `cut_first3.ogg` 校正過**，
`04a_zscore_visualization.py` 這支腳本存在的目的，就是在你相信某份「別的」
錄音的 frame 偵測結果之前，先確認這個門檻在那份錄音上還合不合理——細節見下面
「換一份新錄音時的流程」。

### 2. HDLC destuff（bypass method）——只用來定位結構

跟 `03` 的「bypass method」完全一樣：直接對原始 $Y$ 用固定 threshold=0 逐 bit
做 destuff，同時記錄每個存活下來的 bit 原本對應的 sample index。這一步只負責
「哪些 sample 是資料、哪些是被塞位元刪掉的」以及後續每一步都會用到的 byte
對齊，**不負責**決定這個 bit 最終回報的數值（那是第 3 步的事）。

### 3. 每個段落各自的 bit 判決規則（同一條 bitstream，沿用 02/03 各自的方法）

從第 2 步得到的結構出發，每個段落的「回報出來的 bit 數值」是各自獨立重新判定的：

- **Header（Dest/Src address、Control、PID）+ data1/data2 + FCS**：用
  $y[n] > \theta^*$ 重新判定，跟 `03b`/`03c` 用的是同一個校正閾值。FCS 依照
  `../GNURADIO_MIGRATION.md` 的做法跟著 data 段一起處理。
- **zero-run1/zero-run2**（理論上應該全是 `0x00` 填充）：用
  $y_c[n] > 0$ 重新判定——這其實就是 `02_zero_run_baseline` 的 Method 1，只是
  這裡套用到這個 frame 對應好的 sample index 上而已。（一開始有先試過直接用
  原始 $Y$，結果 zero-run 裡出現約 42% 的 1，誤判率太高——原來 baseline
  restoration 對這個段落是必要的，跟 data1/data2 剛好相反（data1/data2 反而
  是原始 $Y$ 對齊比較乾淨）。完整推理過程寫在腳本自己的 docstring 裡。）

### 4. 標記低可信度的 bit（「可能判決錯誤」/ `Bits` 欄裡的紅字）

$$
\text{header/data/FCS 的 bit 被標記} \iff \lvert y[n]-\theta^*\rvert \le m
$$

（跟 `03b`/`03c` 一樣的「接近判決線」判斷方式），而

$$
\text{zero-run 的 bit 被標記} \iff \hat b[n] = 1
$$

（這個段落理論上應該全是 0，所以只要判成 1 就是 `02` 說的「孤立 1」——這裡是
**全部**都標記出來，不像 `02` 自己的圖只圈出一小部分）。

### 5. 把 bit 對應到 beacon 欄位上

Info 欄位總共 2048 個判決完的 bit，依照 `SCIONX_TLMnew.xlsx` 每一列的
$[\mathrm{OffsetBit}, \mathrm{OffsetBit}+\mathrm{BitLen})$ 切出來，MSB 在前
（緊密排列、中間沒有空隙）。跨多個 byte 的欄位（$\mathrm{BitLen}>8$）如果
`Endian=LE` 會先把 byte 順序反過來。205 個欄位裡有 2 個剛好跨在兩個段落的
邊界上（`CMD Loss Timer` 跨 data1/zero-run1、`EPS UHF7V Current` 跨
data2/zero-run2）——每個 bit 各自的判決（第 3 步）還是對的，只是輸出表格
「整列上同一種底色代表來源段落」（藍=header、桃=data1、粉=data2、
灰/深灰=zero-run1/zero-run2、紫=FCS）這種呈現方式沒辦法表示「一列跨兩個
段落」，這種情況會退回標成黃色。

### 6. 幫每個欄位查出「意思」（`ReadableValue` / 最後一欄）

數值本身的解碼是精確的；`ReadableValue` 是拿這個欄位去對照
`SCIONX_enums.json`、以及欄位自己的 `LongDescription`（如果有的話）查出來的
（詳細比對順序見程式裡的 `resolve_lookup`）。涵蓋率不是百分之百——很多欄位
查不到對照，就只顯示解出來的原始數值。**這一欄只能當參考，不是權威解讀**。

## Frame 對齊自我檢查（不依賴 CRC）

上面第 2 步跟第 5 步都預設「destuff 完的 bit 流剛好切在 frame 邊界上」——也就是
連續 5 個 1 的規則吃掉的 bit 數不多不少。萬一不是這樣，滑移點之後的每個 byte
邊界都會位移，欄位表就會安靜地讀到錯的 bit。CRC 沒辦法告訴你錯在**哪裡**
（它只會 fail），所以解碼器另外跑一個結構性檢查：

**錨點**：合法的 HDLC frame 後面緊接著就是結束 flag。所以 destuff 後的第
$274\times8 = 2192$ 個 bit 必須是一段 `0x7E` 的開頭。檢查會找**連續至少 3 個**
背靠背的 flag（單獨一個 `01111110` 可能在 payload 裡碰巧出現，但間隔**剛好**
8 bits 的一串就不可能是巧合），然後回報

$$\text{滑移量} = (\text{flag 串起始位置}) - 2192$$

這就是 destuff 到底多吃或少吃了幾個 bit。

**定位滑移點**：zero-run 填充區段理論上全是 `0x00`，而填充的 0 只會出現在連續
五個 1 之後——這在全零的區段裡不可能發生。所以只要有移除動作落在 zero-run
裡面，那就**可以證明**是誤刪（bit error 假造出一段 5 個 1），第一個這種誤刪就是
第一個可偵測的滑移點。如果滑移發生在 data 段裡就沒辦法這樣定位，因為那裡沒有
已知內容可以比對——這種情況會誠實回報「無法定位」，不會用猜的。

結果會出現在三個地方：工作表最上方的橫幅、第一個受影響欄位那一列的紅色
`<<< BYTE ALIGNMENT SLIPS AT OR BEFORE THIS FIELD >>>` 標記、以及
`Frame_Info` 分頁裡的 `align_*` 欄位。

## 腳本

| 腳本 | 輸出 |
|---|---|
| `04a_zscore_visualization.py` | 畫出整份錄音的 frame 偵測 z-score 曲線跟 $Z_{\text{th}}$ 對照：`Figure/04a_zscore_<音檔名稱>.png` |
| `04_beacon_field_decode.py` | 把一份錄音裡偵測到的每一個 frame（預設：`../Data/cut_first3.ogg`，全部 3 個）對照 `SCIONX_TLMnew.xlsx` 解碼；每個 frame 各存一份：`Output/<音檔名稱>_frame<N>_beacon_decode.xlsx` |
| `04b_stuffing_events.py` | 列出某個 frame 裡每一個 bit-stuffing 移除事件，附上足夠的上下文（波形視窗、段落、是否低信心）方便手動判斷是真的 stuff bit 還是誤判；只有 console 輸出，不產生 Excel |
| `04c_raw_bits_no_destuff.py` | 跟 `04_beacon_field_decode.py` 同一套流程，但完全跳過 destuff，作為獨立交叉驗證，看 byte 對齊實際是在哪裡開始跑掉的：`Output/<音檔名稱>_frame<N>_raw_no_destuff.xlsx` |
| `04d_destuff_interactive.py` | 把 `scionx_destuff_demo.xlsx` 的機制套到真實訊號，做成一個 ~2200 個原始 bit 的互動工作簿（預設 frame 2）：RawBits 分頁的 ExcludeThisBit/FlipThisBit 下拉選單全部預設 No（不預先猜答案），即時連動到 Beacon Decode 欄位檢視，以及用 Excel 公式算的逐 bit CRC-16/X.25 檢查。`Output/<音檔名稱>_frame<N>_destuff_interactive.xlsx` |
| `04d_destuff_interactive_ver2.py` | `04d_destuff_interactive.py` 的實驗分支，刻意跟主版本並存：同一份資料，但用另一種方式呈現「調整 destuff 時東西會跟著移動」——把壓縮後的 destuffed-position 分頁改成顯示（來源列在固定的 position 下面滑動），而不是像主版本那樣讓 RawBits 每一列固定、只有欄位標籤跟著滑動。`Output/<音檔名稱>_frame<N>_destuff_interactive_ver2.xlsx` |

以上腳本共用同一套 frame 偵測邏輯（上面演算法第 1 步），第一個命令列參數都是
錄音檔路徑，所以都不是寫死只能跑 `cut_first3.ogg`。

## 換一份新錄音時的流程：先看圖，再決定門檻

$Z_{\text{th}}=12.0$ 只用 `cut_first3.ogg` 校正過，**換一份錄音就不適用**。
拿 `cut_first3.ogg` 原本剪自的那份完整過境錄音
（`satnogs_14459039_2026-07-07T09-57-46.ogg`，303 秒 vs. 35.5 秒）來跑，同樣的
12.0 會卡在真實 frame 群的中間，漏掉好幾個把門檻調低就抓得很乾淨的 frame。

所以只要不是 `cut_first3.ogg`，一律先畫圖、再解碼：

```bash
pip install numpy matplotlib soundfile openpyxl

# 1. 先畫出這份錄音的 z-score 曲線，打開 Figure/04a_zscore_<檔名>.png 看
python 04a_zscore_visualization.py path/to/other.ogg

# 2. 挑一個能把尖峰跟背景乾淨分開的門檻，再畫一次確認抓到的數量合理
python 04a_zscore_visualization.py path/to/other.ogg --z-threshold 10.5

# 3. 用決定好的門檻正式解碼
python 04_beacon_field_decode.py path/to/other.ogg --z-threshold 10.5
```

這裡最值得信任的就是那張圖：真的 frame 尖峰又高又窄、跟背景差異一眼就看得
出來，門檻該畫在哪裡用看的比用算的還準。另一個好用的交叉驗證：這顆衛星是
固定週期發 beacon 的（`cut_first3.ogg` 裡相鄰 frame 間隔約 561035 samples），
門檻抓對的話，偵測到的起點位置通常會落在這個間隔的整數倍上。

## 互動式 destuff 流程：04a -> 04b -> 04d

上面提到的三支腳本（`04a`、`04b`、`04d`/`04d_ver2`）是設計成照這個順序、當成
同一套流程一起跑的，不是各自獨立使用：

```bash
# 1. 先確認這份錄音有幾個 frame，決定 z-threshold
#    （跟上面「換一份新錄音時的流程」同一步——如果是 cut_first3.ogg 本身，
#    預設的 12.0 / 3 個 frame 就已經對了，這一步比較像是確認一下，不是真的要搜尋）
python 04a_zscore_visualization.py path/to/audio.ogg --z-threshold 12.0

# 2. 看 04b 自己那套獨立、不做 destuff 的分析，找到你關心的那個 frame
#    有幾個 bit-stuffing 移除事件，以及每個事件的上下文
#    （波形視窗、段落、是否低信心）
python 04b_stuffing_events.py path/to/audio.ogg --frame 2 --z-threshold 12.0

# 3. 用同一組 frame/threshold 產生互動工作簿
python 04d_destuff_interactive.py path/to/audio.ogg --frame 2 --z-threshold 12.0
```

第 2 步不是跑第 3 步的必要條件（04d 自己會做一次獨立的 offset 搜尋），但建議
先做：可以先拿到一個獨立算出來的「大概有幾個、大概在哪裡」的 stuff bit 數量，
等你進到工作簿裡、對下面第 5 步的個別 `CandidateStuffPoint` 標記半信半疑時，
這個數字會是有用的參考基準。

打開工作簿之後，RawBits 的判斷是純手動的（04d 刻意把每一列的
`ExcludeThisBit`/`FlipThisBit` 都留在 No——細節見它自己的 `使用說明` 分頁——
不會先幫你猜答案）：

4. **先看結尾 trailing 那幾列，抓一下目前大概偏差多少。** RawBits 最後
   大約 40 列（`SegmentGuess`=`trailing`）落在 payload+FCS 邊界之後，理論上
   訊號應該是連續的 `0x7E`（`01111110`）結尾旗標。掃一下這個 8-bit pattern
   實際上從哪裡開始出現：如果一開始就對得上，代表你目前的 destuff/flip
   判斷大致上跟真正的 frame 長度一致；如果偏移了 N 個 bit，那大概就是還有
   幾個 stuff bit 的判斷是錯的（一個該排除卻沒排除的真 stuff bit，會讓它
   之後全部往後多推 1 bit；一個不該排除卻排除掉的，會往前多推 1 bit）——
   在你逐一 bit 判斷之前，先做一次全 frame 等級的便宜檢查。
5. **每個 `CandidateStuffPoint`/`LowConfidence` 標記，依照它所在的段落個別
   判斷。** 如果候選點落在 zero-run 段落，可以直接套用下面「判斷封包內容
   正確性的小技巧」同一條經驗法則來判斷這個「1」該不該存在：附近還有其他
   「1」的，比較可能是訊號本來就有的內容（保留它，不要輕易把那個連續 5 個 1
   判成 stuff point）；孤零零、附近完全沒有其他「1」的，比較可能是判決錯誤，
   值得翻轉。落在 zero-run 以外（header/data1/data2）的話，就靠
   `CandidateStuffPoint` 本身的即時訊號（只有在目前 `RawBitValue` 欄真的
   出現連續 5 個 1 時才會 Yes），加上選定之後 Beacon Decode 分頁的
   `ReadableValue` 看起來合不合理來判斷——完整的判斷邏輯，還有最終仲裁用的
   CRC-16/X.25 檢查，見 04d 自己的 `使用說明` 分頁。

## 主要發現（`cut_first3.ogg` 全部 3 個 frame）

- **3 個 frame 的 header 都解得完全正確**：Dest Address = `'BN0CU '`
  （SSID byte=0x60）、Src Address = `'BN0SCX'`（SSID byte=0xE1）、
  Control=`0x03`、PID=`0xF0`——都跟 ground-test 參考值完全吻合，證實
  header 跟 Info 欄位第一個 bit（`APID`，OffsetBit=0）之間沒有漏掉任何東西。
- **各段落「可能判決錯誤」（紅字）的 bit 數，分母都是 2192**：

  | Frame | header | data1 | zero-run1 | data2 | zero-run2 | FCS | **總計** |
  |---|---|---|---|---|---|---|---|
  | #1 | 3/112 | 25/488 | 6/664 | 13/384 | 14/528 | 2/16 | **63/2192（2.9%）** |
  | #2 | 1/112 | 11/488 | 5/664 | 6/384 | 3/528 | 0/16 | **26/2192（1.2%）** |
  | #3 | 1/112 | 33/488 | 10/664 | 21/384 | 8/528 | 2/16 | **75/2192（3.4%）** |

  三個裡面 frame#2 偵測 z-score 最高、header 也是 0/144 錯誤，低可信度 bit
  數也明顯最少——單純是這三次裡收訊最乾淨的一次，跟這個解碼步驟本身沒有
  特別關係。
- **不管用哪種判決方式，3 個 frame 都過不了 CRC**（不管是原始 threshold=0
  的 baseline，還是這個腳本用的 theta*/yc 混合判決都一樣）——配合上面偏低
  的紅字比例，代表大部分 bit 是「判得很有信心」，但不代表「判得正確」。
  單看判決置信度低沒辦法解釋 CRC 一直過不了的原因。
- **3 個 frame 沒有任何一個從頭到尾 byte 對齊**——結束 flag 從來沒有落在
  payload bit 2192 上：

  | Frame | 移除的填充 0 | 結束 flag 位置 | 滑移量 | 第一個可偵測滑移點 |
  |---|---|---|---|---|
  | #1 | 13 | bit 2196 | **+4 bits** | payload byte 144 |
  | #2 | 11 | bit 2189 | **−3 bits** | payload byte 144 |
  | #3 | 8 | bit 2209 | **+17 bits** | payload byte 111 |

  （參考封包正確的填充位數剛好是 **8** 個，所以 #1/#2 多刪掉的那幾個根本不是
  真的填充位）。尾端 flag 明明就在——它們是間隔剛好 8 bits 的一整串——只是沒有
  落在「正確 destuff 完的 274 byte frame」該有的位置上。原因是 data 段的
  bit error 假造出「連續 5 個 1」，destuffer 就刪掉一個根本不是填充的 0，
  byte 網格從那裡開始整個位移。

  **這件事比上面那些逐 bit 置信度數字更重要**：它代表欄位表只有在第一個滑移點
  之前是可信的，而且那個點每個 frame 都不一樣。這也很可能單獨就足以解釋 CRC
  為什麼過不了——一個 byte 邊界在中途就跑掉的 frame，不管個別 symbol 判得多有
  把握，都不可能算出對得上的 FCS。

## 判斷封包內容正確性的小技巧

除了自動標紅（上面演算法第 4 步）之外，每一列的底色代表這一列的 bit 是從
哪個段落來的（上面演算法第 5 步）——先對照一下顏色，底下三個經驗法則用起來
會比較順手：

- 藍色 = header（Dest/Src address、Control、PID）
- 桃色 = data1
- 粉色 = data2
- 灰色 = zero-run1
- 深灰色 = zero-run2
- 紫色 = FCS
- 黃色 = 這個欄位跨了兩個段落的邊界（205 個欄位裡有 2 個是這樣）

0. **先看工作表最上方的對齊橫幅**。如果它顯示 ALIGNMENT FAILED，那就只有紅色
   滑移標記**以上**的列是踩在正確 byte 邊界上的，標記以下讀到的都是位移過的
   bit，再怎麼逐 bit 檢查也救不回來。
1. **主要該花心力判斷的是 data1/data2 的內容**——這是訊號最不可靠的段落
   （`03` 說的「隱藏曲線」不對稱漂移、2-7% 的判決會隨閾值改變），header 跟
   zero-run 相對穩定得多，人工複查的力氣應該集中在 data1/data2 上。
2. **Header 基本上不用花時間確認**——Dest/Src address、Control、PID 是
   協定裡固定的內容，同一顆衛星傳下來的每個封包都一樣（見
   `../REFERENCE_FRAME.md`）。如果這裡解錯了，代表的是 pipeline 別的地方
   對齊出了問題，不是靠盯著這次的 header 本身能解決的。
3. **zero-run1/zero-run2 裡出現的「1」，要看它附近有沒有其他「1」，不要
   單獨看**：如果這個「1」附近沒有其他「1」，通常就是判決錯誤——這個段落
   理論上真的是全零。但如果這個「1」附近還有好幾個「1」聚在一起，比較可能
   是訊號裡真的有非零內容，不是雜訊誤判（這跟 `02_zero_run_baseline` 自己
   的發現一致：真正的填充區段判決起來非常乾淨，孤立的「1」才是異常，一群
   聚在一起的「1」反而不是）。

## 怎麼跑

```bash
python 04_beacon_field_decode.py                       # ../Data/cut_first3.ogg 裡偵測到的每個 frame
python 04_beacon_field_decode.py --frame 2              # 只跑第 2 個
python 04_beacon_field_decode.py path/to/other.ogg --z-threshold 13.5
python 04_beacon_field_decode.py path/to/other.ogg --frame 1 --z-threshold 13.5
```

不需要 GNU Radio。需要 `../scionx/`（`audio_io.py`、`baseline.py`、
`hdlc.py`），會自動找到。Frame 編號純粹依照 sample 位置排序（最早出現的是
frame#1），跟 01/02/03 是同一套慣例——不是寫死只認得 `cut_first3.ogg` 裡那
3 個已知的 frame。輸出檔名前面會加上音檔名稱（例如
`cut_first3_frame2_beacon_decode.xlsx`），這樣換不同錄音檔跑不會互相覆蓋。
如果上一次的輸出檔還開在 Excel 裡，存檔會因為權限問題失敗，記得先關掉。
