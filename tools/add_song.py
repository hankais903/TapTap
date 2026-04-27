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

    # ---- HARD ----
    chart = []
    prev = -1
    for i, t in enumerate(beat_times):
        lane = (i * 3 + 1) % 4
        if lane == prev:
            lane = (lane + 1) % 4
        chart.append({'time': float(t), 'lane': lane, '_strong': True})
        prev = lane

    existing = sorted([c['time'] for c in chart])
    for i, t in enumerate(onset_times):
        if str_norm[i] < hard_threshold:
            continue
        idx = np.searchsorted(existing, t)
        min_dist = float('inf')
        if idx < len(existing):
            min_dist = min(min_dist, abs(existing[idx] - t))
        if idx > 0:
            min_dist = min(min_dist, abs(existing[idx - 1] - t))
        if min_dist < 0.08:
            continue
        lane = assign_lane(cent_norm[i], prev, rng)
        chart.append({'time': float(t), 'lane': lane})
        prev = lane
        existing.insert(idx, float(t))

    # 雙押
    chart.sort(key=lambda x: x['time'])
    for c in list(chart):
        if not c.get('_strong'):
            continue
        if rng.random() < double_rate:
            chart.append({'time': c['time'], 'lane': (c['lane'] + 2) % 4})

    for c in chart:
        c.pop('_strong', None)
    chart.sort(key=lambda x: (x['time'], x['lane']))
    out['hard'] = chart

    # 全難度共通：移除同軌過密
    for diff_name, diff_chart in out.items():
        cleaned = []
        last = {}
        for n in diff_chart:
            if n['lane'] in last and n['time'] - last[n['lane']] < 0.08:
                continue
            cleaned.append(n)
            last[n['lane']] = n['time']
        out[diff_name] = cleaned

    return out


# ========== Audio analysis ==========
def analyze_audio(path, fixed_bpm=None):
    print(f"  Loading {path.name}...")
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    duration = len(y) / sr

    if fixed_bpm:
        tempo = float(fixed_bpm)
        # 用固定 BPM 算等距 beat
        beat_dur = 60.0 / tempo
        beat_times = np.arange(0, duration, beat_dur)
        print(f"  BPM (fixed): {tempo}, beats: {len(beat_times)}")
    else:
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        tempo = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)
        print(f"  BPM (detected): {tempo:.1f}, beats: {len(beat_times)}")

    onset_frames = librosa.onset.onset_detect(y=y, sr=sr, units='frames', backtrack=True)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
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
def update_index(songs_dir: Path, song_id: str, entry: dict):
    index_path = songs_dir / 'index.json'
    if index_path.exists():
        with open(index_path, encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {'songs': []}

    data['songs'] = [s for s in data['songs'] if s['id'] != song_id]
    data['songs'].append(entry)
    data['songs'].sort(key=lambda s: s.get('title', '').lower())

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== Main ==========
def main():
    parser = argparse.ArgumentParser(description='Add a song to NEON BEAT.')
    parser.add_argument('audio', help='Path to mp3 (or wav/ogg/m4a)')
    parser.add_argument('--title', help='Song title (default: file name)')
    parser.add_argument('--artist', default='Unknown', help='Artist (default: Unknown)')
    parser.add_argument('--id', help='Song folder name (default: derived from title)')
    parser.add_argument('--bpm', type=float, help='Force BPM (skip auto-detect)')
    parser.add_argument('--hard-density', type=float, default=0.15,
                        help='Hard onset threshold 0.05-0.5 (default 0.15, lower = denser)')
    parser.add_argument('--double-rate', type=float, default=0.25,
                        help='Hard double-tap rate 0-1 (default 0.25)')
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

    if song_dir.exists():
        print(f"  ! Folder exists, overwriting: {song_dir}")
    song_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nAdding song: {title} ({song_id})")
    audio_filename = 'audio' + audio_path.suffix.lower()
    target_audio = song_dir / audio_filename
    shutil.copy(audio_path, target_audio)

    info = analyze_audio(audio_path, fixed_bpm=args.bpm)
    charts = generate_charts(
        info['beat_times'], info['onset_times'],
        info['str_norm'], info['cent_norm'],
        hard_threshold=args.hard_density,
        double_rate=args.double_rate,
    )

    meta = {
        'id': song_id,
        'title': title,
        'artist': args.artist,
        'bpm': info['bpm'],
        'duration': info['duration'],
        'audio': audio_filename,
    }
    with open(song_dir / 'meta.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(song_dir / 'charts.json', 'w', encoding='utf-8') as f:
        json.dump({'charts': charts}, f, ensure_ascii=False)

    update_index(songs_dir, song_id, meta)

    print()
    for diff in ['easy', 'normal', 'hard']:
        n = len(charts[diff])
        print(f"  {diff:>6}: {n:4d} notes  ({n / info['duration']:.2f} n/s)")
    print(f"\n✓ Done. Folder: songs/{song_id}/")
    return 0


if __name__ == '__main__':
    sys.exit(main())
