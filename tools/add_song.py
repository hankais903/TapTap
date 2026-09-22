#!/usr/bin/env python3
"""
add_song.py — 把一首 mp3/wav 加進 NEON BEAT。

用法：
    python tools/add_song.py path/to/song.mp3 --title "歌名" --artist "歌手"

可選參數：
    --id       歌曲資料夾名 (英數字)，預設從 title 轉換
    --bpm      手動指定 BPM (跳過自動偵測)
    --hard-density   Hard 譜面的 onset 強度門檻 (0.05-0.5，越低越密)
    --double-rate    Hard 雙押機率 (0-1)
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import librosa
import numpy as np


# ========== Slug ==========
def slugify(text: str) -> str:
    text = re.sub(r'[^\w\s\-\u4e00-\u9fff]', '', text.lower())
    text = re.sub(r'[\s\-]+', '_', text).strip('_')
    if not re.match(r'^[a-z0-9_]+$', text):
        # fallback for non-ASCII titles → use a hash
        import hashlib
        text = 'song_' + hashlib.md5(text.encode()).hexdigest()[:8]
    return text or 'song'


# ========== Lane assignment ==========
def assign_lane(centroid_norm, prev_lane, rng):
    """頻率重心 → 軌道：低頻偏外側、高頻偏內側"""
    if centroid_norm < 0.4:
        candidates = [0, 3]
    elif centroid_norm > 0.6:
        candidates = [1, 2]
    else:
        candidates = [0, 1, 2, 3]
    if prev_lane in candidates and len(candidates) > 1:
        candidates = [c for c in candidates if c != prev_lane]
    return int(rng.choice(candidates))


# ========== Chart generation ==========
def generate_charts(beat_times, onset_times, str_norm, cent_norm,
                    hard_threshold=0.15, double_rate=0.25):
    rng = np.random.default_rng(42)
    out = {}

    # ---- EASY ----
    chart = []
    prev = -1
    for i, t in enumerate(beat_times):
        if i % 2 != 0:
            continue
        lane = (i // 2) % 4
        if rng.random() < 0.3:
            lane = int(rng.integers(0, 4))
        if lane == prev:
            lane = (lane + 1) % 4
        chart.append({'time': float(t), 'lane': lane})
        prev = lane
    out['easy'] = chart

    # ---- NORMAL ----
    chart = []
    prev = -1
    for i, t in enumerate(beat_times):
        lane = i % 4
        if rng.random() < 0.4:
            lane = int(rng.integers(0, 4))
        if lane == prev:
            lane = (lane + 1) % 4
        chart.append({'time': float(t), 'lane': lane})
        prev = lane

    existing = sorted([c['time'] for c in chart])
    for i, t in enumerate(onset_times):
        if str_norm[i] < 0.35:
            continue
        idx = np.searchsorted(existing, t)
        min_dist = float('inf')
        if idx < len(existing):
            min_dist = min(min_dist, abs(existing[idx] - t))
        if idx > 0:
            min_dist = min(min_dist, abs(existing[idx - 1] - t))
        if min_dist < 0.10:
            continue
        lane = assign_lane(cent_norm[i], prev, rng)
        chart.append({'time': float(t), 'lane': lane})
        prev = lane
        existing.insert(idx, float(t))
    chart.sort(key=lambda x: (x['time'], x['lane']))
    out['normal'] = chart

    # ---- HARD / INSANE (同一套組裝邏輯, 只是門檻與雙押率不同) ----
    def dense_chart(threshold, min_gap, dbl_rate, lane_step):
        c_prev = -1
        ch = []
        for i, t in enumerate(beat_times):
            lane = (i * lane_step + 1) % 4
            if lane == c_prev:
                lane = (lane + 1) % 4
            ch.append({'time': float(t), 'lane': lane, '_strong': True})
            c_prev = lane

        existing = sorted([c['time'] for c in ch])
        for i, t in enumerate(onset_times):
            if str_norm[i] < threshold:
                continue
            idx = np.searchsorted(existing, t)
            min_dist = float('inf')
            if idx < len(existing):
                min_dist = min(min_dist, abs(existing[idx] - t))
            if idx > 0:
                min_dist = min(min_dist, abs(existing[idx - 1] - t))
            if min_dist < min_gap:
                continue
            lane = assign_lane(cent_norm[i], c_prev, rng)
            ch.append({'time': float(t), 'lane': lane})
            c_prev = lane
            existing.insert(idx, float(t))

        # 雙押 (在強拍處加一顆對側的)
        ch.sort(key=lambda x: x['time'])
        for c in list(ch):
            if not c.get('_strong'):
                continue
            if rng.random() < dbl_rate:
                ch.append({'time': c['time'], 'lane': (c['lane'] + 2) % 4})

        for c in ch:
            c.pop('_strong', None)
        ch.sort(key=lambda x: (x['time'], x['lane']))
        return ch

    out['hard'] = dense_chart(hard_threshold, 0.08, double_rate, 3)
    # INSANE: onset 門檻減半 (抓更多細碎音)、間隔放寬到 60ms、雙押率 1.6 倍
    out['insane'] = dense_chart(hard_threshold * 0.5, 0.06,
                                min(1.0, double_rate * 1.6), 1)

    # 全難度共通：移除同軌過密 (insane 允許更密一點)
    for diff_name, diff_chart in out.items():
        gap = 0.06 if diff_name == 'insane' else 0.08
        cleaned = []
        last = {}
        for n in diff_chart:
            if n['lane'] in last and n['time'] - last[n['lane']] < gap:
                continue
            cleaned.append(n)
            last[n['lane']] = n['time']
        out[diff_name] = cleaned

    return out


# ========== Beat grid fitting ==========
# librosa.beat.beat_track 回傳的 tempo 被量化在 512/22050 s 的整數倍週期上
# (例如 112.347 / 117.454 / 107.666), 跟真實 BPM 差 0.2~2.5%。拿它做等距格子,
# 每拍差 1~11ms, 幾十秒後就漂掉半拍 —— 這就是舊版 16 首裡 11 首「對不到拍」的原因。
# 所以改成: 對高解析 (hop 128 ≈ 5.8ms) 的 onset 包絡做 0.01 BPM 解析度的等距格子擬合,
# 同時搜尋相位; 擬合結果離整數 BPM 很近時 (DAW 做的歌幾乎都是整數) 就吸附到整數。
FIT_HOP = 128
LIBROSA_ONSET_LAG = 0.031   # 秒


def _norm_env(env, sr, hop):
    w = max(1, int(round(1.0 * sr / hop)))
    k = np.ones(w) / w
    loc = np.sqrt(np.convolve(env ** 2, k, mode='same')) + 1e-6
    e = env / loc
    return np.clip(e, 0, np.percentile(e, 99.5))


def _grid_score(env, sr, hop, bpm, phase, duration):
    period = 60.0 / bpm
    t = phase + np.arange(0, int((duration - phase) / period)) * period
    f = np.round(t * sr / hop).astype(int)
    f = f[(f >= 0) & (f < len(env))]
    return float(env[f].mean()) if len(f) else 0.0


def fit_beat_grid(env, sr, hop, bpm0, duration, span=4.0, step=0.01):
    """回傳 (bpm, phase, score): 在 bpm0 ± span 內找最貼合 onset 包絡的等距格子"""
    env = _norm_env(env, sr, hop)
    best = (-1.0, bpm0, 0.0)
    # 粗掃: 0.1 BPM × 4ms 相位
    for bpm in np.arange(bpm0 - span, bpm0 + span + 1e-9, 0.1):
        period = 60.0 / bpm
        for ph in np.arange(0, period, 0.004):
            sc = _grid_score(env, sr, hop, bpm, ph, duration)
            if sc > best[0]:
                best = (sc, float(bpm), float(ph))
    # 細掃: 0.01 BPM × 1ms 相位, 只在粗掃最佳附近
    sc0, b0, p0 = best
    for bpm in np.arange(b0 - 0.15, b0 + 0.15 + 1e-9, step):
        period = 60.0 / bpm
        for ph in np.arange(max(0, p0 - 0.03), p0 + 0.03, 0.001):
            sc = _grid_score(env, sr, hop, bpm, ph % period, duration)
            if sc > sc0:
                sc0, b0, p0 = sc, float(bpm), float(ph % period)
    # 吸附整數 BPM (差 < 0.15 且分數沒明顯變差)
    bi = float(round(b0))
    if abs(bi - b0) < 0.15:
        period = 60.0 / bi
        phs = np.arange(max(0, p0 - 0.03), p0 + 0.03, 0.001)
        scs = [_grid_score(env, sr, hop, bi, ph % period, duration) for ph in phs]
        j = int(np.argmax(scs))
        if scs[j] >= sc0 * 0.97:
            sc0, b0, p0 = scs[j], bi, float(phs[j] % period)
    return b0, p0, sc0


# ========== Audio analysis ==========
def analyze_audio(path, fixed_bpm=None, first_beat=None):
    print(f"  Loading {path.name}...")
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    duration = len(y) / sr

    # 高解析 onset 包絡, 給格子擬合用
    fine_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=FIT_HOP)

    if fixed_bpm:
        tempo = float(fixed_bpm)
        if first_beat is not None:
            phase = float(first_beat) % (60.0 / tempo)
        else:
            # BPM 給定, 只搜相位
            _, phase, _ = fit_beat_grid(fine_env, sr, FIT_HOP, tempo, duration, span=0.0, step=1.0)
        print(f"  BPM (fixed): {tempo}, first beat: {phase:.3f}s")
    else:
        tempo0, _ = librosa.beat.beat_track(y=y, sr=sr)
        tempo0 = float(tempo0[0]) if hasattr(tempo0, '__len__') else float(tempo0)
        tempo, phase, score = fit_beat_grid(fine_env, sr, FIT_HOP, tempo0, duration)
        print(f"  BPM: librosa 粗估 {tempo0:.2f} → 格子擬合 {tempo:.2f}, first beat {phase:.3f}s (score {score:.2f})")
        if first_beat is not None:
            phase = float(first_beat) % (60.0 / tempo)
    beat_dur = 60.0 / tempo
    beat_times = phase + np.arange(0, int((duration - phase) / beat_dur) + 1) * beat_dur
    beat_times = beat_times[beat_times < duration]
    # librosa.onset.onset_strength (center=True) 的包絡峰值比真正的 transient 系統性晚約 30ms
    # (用瀏覽器解碼的 PCM 做互相關實測: 新歌 +31ms, 舊譜 +35~45ms), 整格提前補償
    beat_times = beat_times - LIBROSA_ONSET_LAG
    beat_times = beat_times[beat_times >= 0]
    print(f"  beats: {len(beat_times)}")

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units='frames', backtrack=True)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    # backtrack 已經把 onset 往前推到能量谷, 不再另外減 lag (實測兩者相抵後 onset 音符略早 ~10ms, 可接受)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_strengths = np.array([onset_env[f] for f in onset_frames])

    spec_centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    onset_centroids = np.array([
        spec_centroid[min(f, len(spec_centroid) - 1)] for f in onset_frames
    ])

    cent_min, cent_max = np.percentile(onset_centroids, [10, 90])
    cent_norm = np.clip((onset_centroids - cent_min) / (cent_max - cent_min + 1e-9), 0, 1)
    str_norm = np.clip(onset_strengths / (np.max(onset_strengths) + 1e-9), 0, 1)

    print(f"  Onsets: {len(onset_times)}, duration: {duration:.1f}s")

    return {
        'duration': float(duration),
        'bpm': float(tempo),
        'beat_times': beat_times,
        'onset_times': onset_times,
        'str_norm': str_norm,
        'cent_norm': cent_norm,
    }


# ========== Update index.json ==========
INDEX_KEYS = ['id', 'title', 'artist', 'bpm', 'duration', 'audio', 'youtube']


def update_index(songs_dir: Path, song_id: str, entry: dict):
    index_path = songs_dir / 'index.json'
    if index_path.exists():
        with open(index_path, encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {'songs': []}

    # 保留舊資料裡手動填的欄位 (youtube 只存在 index/meta, 重跑時不能被洗掉)
    previous = next((s for s in data['songs'] if s.get('id') == song_id), {})
    merged = {k: entry.get(k, previous.get(k, '')) for k in INDEX_KEYS}
    for k in ('youtube',):
        if not merged.get(k):
            merged[k] = previous.get(k, '') or ''

    data['songs'] = [s for s in data['songs'] if s.get('id') != song_id]
    data['songs'].append(merged)
    data['songs'].sort(key=lambda s: (s.get('title') or '').lower())

    # 丟掉資料夾已經不存在的死資料 (刪歌時常忘了同步 index.json)
    alive, dead = [], []
    for s in data['songs']:
        if (songs_dir / s['id'] / 'meta.json').exists():
            alive.append(s)
        else:
            dead.append(s['id'])
    data['songs'] = alive
    for d in dead:
        print(f"  ! 移除死資料: {d} (songs/{d}/meta.json 不存在)")

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return merged


# ========== Main ==========
def main():
    parser = argparse.ArgumentParser(description='Add a song to NEON BEAT.')
    parser.add_argument('audio', help='Path to mp3 (or wav/ogg/m4a)')
    parser.add_argument('--title', help='Song title (default: file name)')
    parser.add_argument('--artist', default='Unknown', help='Artist (default: Unknown)')
    parser.add_argument('--id', help='Song folder name (default: derived from title)')
    parser.add_argument('--bpm', type=float, help='Force BPM (skip auto-detect; phase is still fitted)')
    parser.add_argument('--first-beat', type=float, dest='first_beat',
                        help='Force first beat time in seconds (skip phase fitting)')
    parser.add_argument('--hard-density', type=float, default=0.15,
                        help='Hard onset threshold 0.05-0.5 (default 0.15, lower = denser)')
    parser.add_argument('--double-rate', type=float, default=0.25,
                        help='Hard double-tap rate 0-1 (default 0.25)')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite an existing song folder (charts.json included)')
    args = parser.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        print(f"Error: file not found: {audio_path}", file=sys.stderr)
        return 1

    title = args.title or audio_path.stem
    song_id = args.id or slugify(title)

    root = Path(__file__).resolve().parent.parent
    songs_dir = root / 'songs'
    songs_dir.mkdir(exist_ok=True)
    song_dir = songs_dir / song_id

    # 手編過的譜面不該被無聲蓋掉
    if (song_dir / 'charts.json').exists() and not args.force:
        print(f"Error: songs/{song_id}/charts.json already exists.", file=sys.stderr)
        print("       Re-running would discard any hand-edited chart.", file=sys.stderr)
        print("       Use --force to overwrite, or --id to pick another folder name.",
              file=sys.stderr)
        return 1
    if song_dir.exists():
        print(f"  ! Folder exists, overwriting: {song_dir}")
    song_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nAdding song: {title} ({song_id})")
    audio_filename = 'audio' + audio_path.suffix.lower()
    target_audio = song_dir / audio_filename
    shutil.copy(audio_path, target_audio)

    info = analyze_audio(audio_path, fixed_bpm=args.bpm, first_beat=args.first_beat)
    charts = generate_charts(
        info['beat_times'], info['onset_times'],
        info['str_norm'], info['cent_norm'],
        hard_threshold=args.hard_density,
        double_rate=args.double_rate,
    )

    # 沿用既有 meta 的手填欄位 (youtube), 不要每次重跑都清掉
    meta_path = song_dir / 'meta.json'
    previous_meta = {}
    if meta_path.exists():
        try:
            with open(meta_path, encoding='utf-8') as f:
                previous_meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            previous_meta = {}

    meta = {
        'id': song_id,
        'title': title,
        'artist': args.artist,
        'bpm': round(info['bpm'], 2),
        'firstBeat': round(float(info['beat_times'][0]), 3) if len(info['beat_times']) else 0.0,
        'duration': info['duration'],
        'audio': audio_filename,
        'youtube': previous_meta.get('youtube', '') or '',
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write('\n')
    with open(song_dir / 'charts.json', 'w', encoding='utf-8') as f:
        json.dump({'charts': charts}, f, ensure_ascii=False)

    # index.json 必須跟 meta.json 一致 — 兩邊漂掉會讓編譜器讀到錯的 BPM / 長度
    update_index(songs_dir, song_id, meta)

    print()
    for diff in ['easy', 'normal', 'hard', 'insane']:
        n = len(charts[diff])
        print(f"  {diff:>6}: {n:4d} notes  ({n / info['duration']:.2f} n/s)")
    print(f"\n✓ Done. Folder: songs/{song_id}/")
    return 0


if __name__ == '__main__':
    sys.exit(main())
