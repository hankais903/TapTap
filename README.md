# NEON BEAT — 多歌節奏遊戲

落下式 4 軌節奏遊戲。每首歌會產生四個難度的譜面（Easy / Normal / Hard / Insane），
含分數、判定、Combo、長按音符、最高分紀錄。

## 目錄結構

```
neon-beat/
├── index.html              # 遊戲主程式（單一檔案，純 HTML/CSS/JS）
├── editor.html             # 手動編譜器 + 校拍工具
├── settings.json           # 各難度的落速與判定窗
├── songs/
│   ├── index.json          # 歌曲清單（add_song.py 自動維護）
│   └── midnight_filter/    # 每首歌一個資料夾
│       ├── audio.mp3       # 音檔
│       ├── charts.json     # 四難度譜面
│       └── meta.json       # 標題、BPM、長度（單一真相來源）
└── tools/
    └── add_song.py         # 把新 mp3 加進清單的工具
```

> `meta.json` 是每首歌 metadata 的單一真相來源，`index.json` 只是它的快取清單。
> 兩邊漂掉的話遊戲會用 meta、編譜器也會用 meta，但請用 `add_song.py` 讓它們保持一致。

## 怎麼跑

瀏覽器的安全策略不允許 HTML 用 file:// 協定載入其他檔案，所以要起一個本地伺服器：

```bash
cd neon-beat
python3 -m http.server 8000
```

然後打開 http://localhost:8000

或者用 Node.js：

```bash
npx serve
```

或者直接部署到 GitHub Pages / Vercel / Netlify (免費的靜態網站服務都可以)。

## 怎麼新增一首歌

```bash
python3 tools/add_song.py /path/to/your_song.mp3 \
  --title "歌名" \
  --artist "歌手" \
  --id "english_id"
```

工具會自動：
1. 把 mp3 複製到 `songs/<id>/audio.mp3`
2. 用 librosa 分析 BPM、節拍、音點起始
3. 產生三難度譜面 → `charts.json`
4. 更新 `songs/index.json`

需要先安裝相依套件：
```bash
pip install librosa numpy
```

### 進階參數

| 參數 | 說明 | 預設 |
|---|---|---|
| `--bpm 128` | 強制指定 BPM (跳過自動偵測) | 自動 |
| `--hard-density 0.1` | Hard 譜面密度 (0.05–0.5，越低越密) | 0.15 |
| `--double-rate 0.4` | Hard 雙押機率 (0–1) | 0.25 |
| `--force` | 蓋掉已存在的歌曲資料夾（含手編過的 charts.json） | 關閉 |

沒有 `--force` 時，如果 `songs/<id>/charts.json` 已經存在就會直接中止，避免手編的譜面被自動生成的蓋掉。
`youtube` 欄位重跑時會保留。

如果 librosa 自動偵測的 BPM 是實際 BPM 的兩倍 / 半倍 (常見問題)，用 `--bpm` 強制覆寫。

## 操作

- **D F J K** 對應四個軌道，從左到右
- 手機 / 觸控可以直接點軌道下半部，支援多指同時按
- 長按音符要按住到尾巴：按滿 95% 以上給 `HOLD!` 加 combo，中途放開只是少拿分，不會斷 combo
- 判定窗預設 Perfect ±60ms / Good ±110ms / Okay ±160ms（四個難度相同，見 `settings.json`）
- 進設定頁可以自己調落速與判定窗，會存在 localStorage；三個判定窗會自動維持 Perfect ≤ Good ≤ Okay
- 音樂開始後有 4 秒 intro（`CONFIG.introDelay`），這段時間內的音符不會出現
- 最高分會存在瀏覽器 localStorage，不同瀏覽器各自獨立

## 譜面是怎麼自動生成的

```
mp3 → librosa
       ├── beat_track       → 抓拍點 (節奏骨架)
       ├── onset_detect     → 抓音符起始 (鼓點/旋律重音)
       └── spectral_centroid → 各音點的頻率重心

譜面組裝
       ├── Easy: 每兩拍一顆 (跟主節拍)
       ├── Normal: 每拍一顆 + 中強 onset
       ├── Hard: 全 onset + 雙押 (在強拍處)
       └── Insane: onset 門檻減半 + 雙押率 1.6 倍 + 同軌間隔放寬到 60ms

軌道分配規則
       ├── 低頻 (鼓 / bass)   → 外側 D / K
       ├── 高頻 (hihat / 旋律) → 內側 F / J
       └── 防呆: 不連續同軌、同軌間隔 ≥ 80ms
```

## 不同難度的差異

不只是音符數變多，**落速 + 判定窗也跟著調整**：

落速由 `settings.json` 決定，判定窗目前四個難度相同 —— 難度差異來自落速與音符密度：

| 難度 | 落速 | Perfect / Good / Okay 窗 |
|---|---|---|
| Easy | 1.4s（看得很清楚） | ±60 / ±110 / ±160ms |
| Normal | 1.1s | ±60 / ±110 / ±160ms |
| Hard | 0.9s（要快速讀譜） | ±60 / ±110 / ±160ms |
| Insane | 0.75s | ±60 / ±110 / ±160ms |

## 手動編譜 (editor.html)

自動生成的譜面可以再用編譜器手工修。一樣要透過本地伺服器開：`http://localhost:8000/editor.html`

- **編譜模式**：點軌道空白處新增音符、點音符選取、可微調時間 (±5/±10ms)、切換 Hold 並調長度、刪除
- **校拍模式**：邊聽邊按 TAP 記錄拍點（至少 30 拍），會做線性回歸平滑掉手抖，算出 BPM 與 `firstBeat`
- 兩種模式都是**下載檔案**（`<id>_charts.json` / `<id>_meta.json`），要自己覆蓋回 `songs/<id>/`

## 想自己改

- **譜面演算法**：`tools/add_song.py` 的 `generate_charts()`
- **遊戲設定**：`index.html` 的 `CONFIG` 與 `DIFFICULTY_PRESETS`
- **視覺風格**：`index.html` 頂部的 CSS 變數
- **音訊快取上限**：`index.html` 的 `BackgroundMusicPlayer` 的 `maxDecoded` / `maxBytes`
  （解碼後的 AudioBuffer 是未壓縮 PCM，一首 3 分鐘的歌約 60MB，所以預設只留 3 首）

## 已知限制

- librosa 對某些電子樂的 BPM 偵測會半速 / 倍速 → 用 `--bpm` 手動修正
- `--bpm` 固定模式假設第一拍在 0 秒，沒有 offset 參數
- 自動譜面少了人工編譜的「設計感」，但骨幹節奏準確
- 自動生成只產普通音符 (tap)；長按 (hold) 要用 `editor.html` 手動加
- 沒有滑鍵 / 雙線；編譜器沒有 undo
