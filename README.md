# NEON BEAT — 多歌節奏遊戲

落下式 4 軌節奏遊戲。每首歌會自動產生三個難度的譜面（Easy / Normal / Hard），
含分數、判定、Combo、最高分紀錄。

## 目錄結構

```
neon-beat/
├── index.html              # 遊戲主程式（單一檔案，純 HTML/CSS/JS）
├── songs/
│   ├── index.json          # 歌曲清單（add_song.py 自動維護）
│   └── midnight_filter/    # 每首歌一個資料夾
│       ├── audio.mp3       # 音檔
│       ├── charts.json     # 三難度譜面
│       └── meta.json       # 標題、BPM、長度
└── tools/
    └── add_song.py         # 把新 mp3 加進清單的工具
```

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

如果 librosa 自動偵測的 BPM 是實際 BPM 的兩倍 / 半倍 (常見問題)，用 `--bpm` 強制覆寫。

## 操作

- **D F J K** 對應四個軌道，從左到右
- 手機 / 觸控可以直接點軌道下半部
- 判定窗：Perfect ±45ms / Good ±90ms / Okay ±135ms (Normal 難度)
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
       └── Hard: 全 onset + 雙押 (在強拍處)

軌道分配規則
       ├── 低頻 (鼓 / bass)   → 外側 D / K
       ├── 高頻 (hihat / 旋律) → 內側 F / J
       └── 防呆: 不連續同軌、同軌間隔 ≥ 80ms
```

## 不同難度的差異

不只是音符數變多，**落速 + 判定窗也跟著調整**：

| 難度 | 落速 | Perfect 窗 |
|---|---|---|
| Easy | 1.4s（看得很清楚） | ±60ms |
| Normal | 1.1s | ±45ms |
| Hard | 0.9s（要快速讀譜） | ±35ms |

## 想自己改

- **譜面演算法**：`tools/add_song.py` 的 `generate_charts()`
- **遊戲設定**：`index.html` 的 `CONFIG` 與 `DIFFICULTY_PRESETS`
- **視覺風格**：`index.html` 頂部的 CSS 變數 (`--neon-cyan` 等)

## 已知限制

- librosa 對某些電子樂的 BPM 偵測會半速 / 倍速 → 用 `--bpm` 手動修正
- 自動譜面少了人工編譜的「設計感」，但骨幹節奏準確
- 目前只支援普通音符 (tap)，沒有長按 / 滑鍵 / 雙線
