# Recovery_try — 前饋式時脈音估計（混合嘗試）

在 seg010、seg007 這兩段「解不出來」的訊號上，做的最後一次嘗試：結合固定
網格（能解出 seg017/seg002，但假設 sps 剛好等於 5.0）與時序回復（真實數
據機都會用，但這裡的閉環 PLL 反而比固定網格差——HDLC 長串全零填充段完全
沒有轉態，TED 拿不到誤差訊號，環路會亂跑）。

這次改用**前饋、單次、開環**的估計方式：對整段幀只估計一次符元週期 `T`
與取樣相位 `φ`，然後把 `idx(n) = φ + T·n` 當成校正過的固定網格套用下去。
沒有回授環路，不需要 Communications Toolbox。

## 檔案

| 檔案 | 作用 |
|---|---|
| `clock_tone_locate.m` | 估計器本身。平方微分版 Oerder–Meyr 時脈音回復：`g = (Δx)²`，掃描 `S(T)=Σ g·e^{-j2π·pos/T}`，`T̂ = argmax|S(T)|`，相位由 `∠S(T̂)` 求出。每段輸出 6 面板診斷圖 + JSON。 |
| `decode_with_grid.py` | 套用 JSON 給的 `(sps, phase, polarity)` 網格到 wav 上，切片（固定門檻 **或** min-max 動態門檻），跑 HDLC 解幀 + CRC-16/X.25。是 `../print_seg002_decode.py` 的通用化版本。 |
| `seg010_header_ber.py` | 量化 seg010 到底「差多少」：前 16 bytes 是已知常數，統計每個候選幀的 Hamming distance。 |
| `clock_tone_<seg>.png/.fig` | 每段的 6 面板診斷圖。 |
| `clock_tone_<seg>.json` | `{sps_est, phase_sample, polarity, z, ppm, opening_*}`，交給解碼器用。 |

## 方法（為什麼用平方微分，不用 |x|²）

矩形 ±NRZ 波形在符元速率上有**頻譜零點**（PSD 是 sinc²），所以直接鎖定
沒有譜線可用；`|x|²` 對 ±A 的 NRZ 幾乎是常數，重建不出任何東西。**平方
微分** `(dx/dt)²` 會在每次轉態時脈衝，重建出 `1/T` 的強譜線。平坦的全零
段自動貢獻 `g≈0`，不需要額外的 gating 邏輯。這個估計器對振幅**完全不敏
感**（不會重蹈 PLL 的 DetectorGain bug），每個採樣點的位置都是封閉解
（不會重蹈「速率會變的區塊沒有位置資訊」那個 bug）。`|S(T)|` 這條曲線，
配上對自身背景的 z-score，本身就是答案：它直接告訴你這段訊號**有沒有**
可恢復的時脈。

## 結果

| segment | T̂ (samples/sym) | ppm | clock z | drift/frame | fixed 眼圖 | hybrid 眼圖 | CRC |
|---|---|---|---|---|---|---|---|
| seg017（已知能解） | 5.00074 | +149 | **7.0** | 1.64 | +5.77 | +5.52 | **PASS** |
| seg002（已知能解） | 4.99982 | −36  | **7.7** | −0.39 | +1.96 | +1.83 | **PASS** |
| seg010 | 4.99941 | −119 | 4.1 | −1.31 | +0.14 | +0.04 | fail |
| seg007 | 4.96483 | −7034 | 4.2 | −77（無意義） | +0.13 | +0.07 | fail |

**Gate 1（估計器是否有效）**：兩段已知能解的訊號時脈譜線清楚，z≈7–7.7，
`T̂` 落在標稱值 ±150 ppm 內；兩段難解的只有 z≈4——有真實區隔，但沒那麼強。

**Gate 2（不能退步）**：`decode_with_grid.py` 用 hybrid 網格重新解出
seg017 與 seg002——274 bytes、CRC-16/X.25 通過、header 與
REFERENCE_FRAME.md 完全一致（`BN0CU`/`BN0SCX`），使用的 sps 分別是
`5.00074` / `4.99982`（**不是**剛好 5.0）。沒有退步。

**Gate 3（難解段）**：兩段都沒過 CRC，但**原因不同，而且都已經釐清**：

- **seg007 — 沒有可恢復的時脈。** panel 1 完全沒有超出背景的譜線；
  `T=4.9648` 那個「峰值」是假的（`z=4.2`，split-half 一個卡在網格邊緣
  `T_first=4.900`、另一個 `T_second=5.056`，完全不一致）。兩張眼圖都是
  閉合的，波形本身也沒有乾淨的正負軌跡結構。**這段是雜訊，沒有訊噪比可
  言，沒有 timing 可抓。**

- **seg010 — 時脈是真的，但眼睛是閉的（SNR 限制）。** 真實的時脈譜線落
  在標稱值附近（`T̂≈5.000`，split-half 一致），所以 timing **不是**問
  題——但光抓到 timing 沒有用。波形整體確實騎在一個大的直流偏移上（振
  幅落在 −8 到 −10，不是 0——就是 `../header_correlate_locate.m` 註解裡
  記錄的那個 DC step），但即使用能追蹤基線飄移的 min-max 動態切片器去掉
  這個偏移，bit 還是錯的。`seg010_header_ber.py` 是決定性的檢驗：前 16
  bytes header 是固定內容，掃過所有網格／兩種切片器／兩種極性後，26 個
  回收到的 274-byte 候選幀裡，**最好的 header 也錯了 53/128 bits，也就是
  41%，幾乎等於亂猜**，而且每個 header byte 都錯。所以那些「長度剛好對」
  的候選幀，其實是解幀器在雜訊裡剛好湊出類 flag 的圖案，**不是**只差幾
  個 bit 的真實幀。基線還原（baseline restoration）值得一試，但依這個證
  據看，眼睛是被 **SNR** 關死的，機率不高。

## 結論

前饋式時脈音回復對這個訊號來說是**正確**的 timing 方法（贏過 PLL，在已
知數據上追平固定網格，而且真的抓出了 seg017 微小的 +149 ppm 偏差）。但
**timing 從來就不是 seg010／seg007 解不出來的瓶頸。** clock-tone 的
z-score 乾淨地把兩種失敗分開：seg007 完全沒有時脈（死訊號／雜訊），
seg010 有真實時脈但眼睛是閉的——即使做了基線追蹤，header BER 仍然
41%，屬於 SNR 限制而非 timing 限制。**baseline restoration
（`../../02_zero_run_baseline/`、`scionx/baseline.py`）是 seg010 唯一
還值得一試的槓桿，但依 41% 的 BER 判斷，成功機率不高。繼續在 timing
recovery 上下功夫不會解出這兩段。**

## 重現方式

```matlab
cd D:\Research\3_SCIONX GNURadio\1_Share\appendix_gnuradio\Recovery_try
clock_tone_locate          % 產生 clock_tone_<seg>.png/.fig/.json
```
```bash
cd Recovery_try
python decode_with_grid.py ../Output/20260723_091639_seg017_510-540s.wav \
    --json clock_tone_20260723_091639_seg017_510-540s.json          # PASS
python decode_with_grid.py ../Output/20260723_091639_seg010_300-330s.wav \
    --json clock_tone_20260723_091639_seg010_300-330s.json \
    --slicer both --scan-phase 2.5 --scan-phase-step 0.25 --dyn-window 16   # fail
python seg010_header_ber.py                                          # seg010 到底差多少？
```

## 兩個不能重踩的 bug（先前害這個專案跑出一輪無效結果）

1. **`comm.SymbolSynchronizer`需要單位振幅的輸入**——這些 wav 是原始
   float（RMS 9–11）；PLL 的 DetectorGain 假設 ~1.0，餵原始振幅會讓它追
   蹤不到任何東西。*這次的前饋估計器對振幅完全不敏感，不受影響——但如
   果之後又回頭用 PLL，這件事仍然成立。*
2. **`comm.SymbolSynchronizer`是速率會變的區塊，沒有輸出每個符元的位置
   資訊**——天真的 `symbol k ↔ sample k·N/M` 映射會漂移，讓採樣點偏離波
   形；需要校正約 +1.5 samples 的 strobe lag，外加逐符元的 `mu` 微調。
   *這次估計器的每個採樣點都是 `φ+T·n` 的封閉解——不受影響。*
