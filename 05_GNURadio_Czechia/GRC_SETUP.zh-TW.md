# 使用 GRC 流程圖（05a）—— 每台機器的一次性前置設定

`05a_IQtoOgg_plot.grc` 用到一個**客製 GRC block：`Plot Capture`**，它隨這個
repo 放在 [`grc_blocks/`](grc_blocks/) 底下。GNU Radio Companion（GRC）**不會**
自動找到客製 block —— 你得在自己的機器上，把它指到那個資料夾一次。這份文件
就是講這個前置設定，以及讓它比看起來更麻煩的那個 Windows 設定檔陷阱。

> 英文版：[`GRC_SETUP.md`](GRC_SETUP.md)。

---

## 1. 你到底需不需要做這個？

| 你想做的事 | 需要這個設定嗎？ |
|---|---|
| **執行**流程圖 —— `python 05a_IQtoOgg_plot.py` | **不用。** |
| 在 `gnuradio-companion`（GUI）裡**打開／編輯** `05a_IQtoOgg_plot.grc` | **要** —— 否則畫布上會出現 `Missing Block  key: plot_capture`。 |

**為什麼有這個差別。** 這是兩套不同的機制：

- **執行時**（跑產生出來的 `.py`）：實際發生的只是 Python 去 `import
  gr_plot_capture`。這由流程圖旁邊那個 2 行的轉接檔
  [`gr_plot_capture.py`](gr_plot_capture.py) 處理，它把這個 import 導向
  `grc_blocks/`。**完全不牽涉任何 GRC 設定**，所以全新 clone、零設定就能跑腳本。
- **設計時**（GRC 要在畫布上畫出這個 block）：GRC 的 block *library* 只會從
  `local_blocks_path` 清單裡的資料夾建立。如果 `grc_blocks/` 不在那個清單裡，
  GRC 根本沒聽過 `plot_capture`，就把它畫成紅色的 **Missing Block**。

所以：**只有想在 GUI 裡編輯流程圖的人需要做這個。** 純粹跑 pipeline 的人可以
整頁跳過。

---

## 2. 為什麼不是自動的，以及為什麼 `git` 帶不動它

block 的*定義檔*（`grc_blocks/plot_capture.block.yml`）**有**在 repo 裡、會跟著
`git` 走。帶不動的是那個「叫 GRC 去這個資料夾找」的設定 —— `local_blocks_path`。
它帶不動有兩個原因：

1. 它存在你**每台機器各自的 GNU Radio 使用者設定檔**（`config.conf`，在你的
   home / AppData 底下），那是 `git` 從來不碰的地方。
2. 它是一個**絕對路徑**，而每個人 clone 這個 repo 的位置都不一樣，所以根本沒有
   一個可以 commit 進去的通用值。

這就是為什麼每個人都要**在本地設一次** —— 你不是在修 repo 的 bug，你是在告訴
*你的* GRC，*你的*那份資料夾放在哪裡。

---

## 3. 前置設定（複製貼上）

**步驟 1。** 打開**你平常用來啟動 `gnuradio-companion` 的那個 shell**（Windows 上
通常是 *radioconda 視窗* —— 這很重要，見 §4），`cd` 進這個資料夾：

```bash
cd <你clone的位置>/05_GNURadio_Czechia
```

**步驟 2。** 跑下面這段。它會幫你算出 `grc_blocks/` 的絕對路徑，**append** 到你
現有的客製 block 路徑後面（所以不會蓋掉你其他的 OOT block），並順手把失效的路徑
清掉：

```python
import os
from gnuradio import gr

blocks = os.path.abspath('grc_blocks')
p = gr.prefs()
# 保留現有、仍存在的路徑；丟掉失效的，以及跟我們重複的那筆
kept = [x for x in p.get_string('grc', 'local_blocks_path', '').split(os.pathsep)
        if x and os.path.isdir(x) and os.path.normpath(x) != os.path.normpath(blocks)]
kept.append(blocks)
p.set_string('grc', 'local_blocks_path', os.pathsep.join(kept))
p.save()
print('local_blocks_path =', p.get_string('grc', 'local_blocks_path', ''))
```

也可以用這行直接貼進任何 shell（`cmd`、PowerShell、bash 都行）：

```bash
python -c "import os; from gnuradio import gr; b=os.path.abspath('grc_blocks'); p=gr.prefs(); k=[x for x in p.get_string('grc','local_blocks_path','').split(os.pathsep) if x and os.path.isdir(x) and os.path.normpath(x)!=os.path.normpath(b)]; k.append(b); p.set_string('grc','local_blocks_path', os.pathsep.join(k)); p.save(); print('local_blocks_path =', p.get_string('grc','local_blocks_path',''))"
```

（如果你 `PATH` 上的 `python` 不是 radioconda 那個，請用完整路徑，例如
`C:\Users\<你>\radioconda\python.exe`。）

**步驟 3。** 如果 `gnuradio-companion` 開著，先**完全關閉它** —— GRC **只在啟動時**
讀一次 `local_blocks_path`，所以正在跑的那個實例不會即時吃到這個改動，而且*在裡面
重新打開檔案也沒用*。然後重新啟動、打開 `05a_IQtoOgg_plot.grc`，`Plot Capture`
就會正常出現，不再是 Missing Block。

**隨時驗證：**

```bash
python -c "from gnuradio import gr; print(gr.prefs().get_string('grc','local_blocks_path',''))"
```

你應該會看到一條以 `...05_GNURadio_Czechia\grc_blocks` 結尾的路徑（以及你原本就有
的其他客製 block 資料夾）。

---

## 4. Windows 陷阱：有兩個設定檔，`HOME` 決定讀哪一個

這是最微妙的地方，也正是這個專案自己踩過的 `Missing Block` 根因。GNU Radio 的
C++ 偏好設定層，用 `HOME` 環境變數來決定要從哪個資料夾讀 `config.conf`：

| 啟動環境 | `HOME` | GNU Radio 讀的設定檔 |
|---|---|---|
| Git-Bash / WSL | **有設** | `C:\Users\<你>\.gnuradio\config.conf` |
| `cmd` / PowerShell / **radioconda 視窗** | **沒設** | `C:\Users\<你>\AppData\Roaming\.gnuradio\config.conf` |

這是**兩個不同的檔案**。所以：

- **安全守則：從*你用來啟動 `gnuradio-companion` 的那個 shell*** 去跑步驟 2 的
  程式。這樣你寫入的，就保證是 GRC 之後會讀的那一份。多數人就是 radioconda 視窗
  （AppData 那份）。
- **如果你會用不只一種方式啟動 GRC**（例如有時 radioconda、有時 Git-Bash），就
  **在每一種各跑一次**步驟 2，讓兩個設定檔一致。要在 Bash 裡強制模擬沒有 `HOME`
  （radioconda）的情況來測，在指令前面加 `env -u HOME`。

另外還有一個誘餌檔 `C:\Users\<你>\.gnuradio\grc.conf`（注意是 `grc.conf`，不是
`config.conf`）。它只存 GUI 的視窗狀態（最近開的檔、面板大小）—— **`local_blocks_path`
不在裡面**，別去改它。

> **不要**手動去改 `config.conf`。一律走步驟 2 的
> `gr.prefs().set_string(...).save()` —— 它會寫到 GNU Radio 針對你目前這個 shell
> 解析出來的那個檔，這是唯一能保證 `gnuradio-companion` 跟 `grcc` 一致的方法。

---

## 5. 還是 `Missing Block  key: plot_capture`？檢查清單

1. **你有完全重啟 GRC 嗎？** 不是只重開 `.grc` —— 是整個程式關掉（確認沒有殘留的
   `gnuradio-companion` 程序）再重開。
2. **你設到對的設定檔了嗎？** 用 §3 的*驗證*指令、**在你啟動 GRC 的那個 shell 裡**
   再跑一次（§4）。如果它沒列出 `...05_GNURadio_Czechia\grc_blocks`，代表你設到了
   另一個檔 —— 在同一個 shell 裡重跑步驟 2。
3. **那個資料夾路徑真的存在、也真的有 block 嗎？** 確認你註冊的那條路徑底下有
   `grc_blocks/plot_capture.block.yml`（clone 被搬動／改名是最經典的原因）。
4. **用 `grcc` 做無頭健檢** —— 它跟 GUI 用同一套 block 載入器，還會印出搜尋路徑：
   ```bash
   grcc -o . 05a_IQtoOgg_plot.grc
   ```
   如果 `grcc` 在 "Block paths" 底下列出 `...05_GNURadio_Czechia\grc_blocks` 且
   exit 0，代表 block 註冊正確，問題出在 GUI 是舊實例（回到第 1 點）。
