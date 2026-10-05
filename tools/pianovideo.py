#!/usr/bin/env python3
"""Turn a falling-notes piano video (Synthesia style) into an Ongaku Renshuu piano song.

It finds the keyboard at the bottom of the video, watches which keys light up, splits the
hands by colour, checks the octave against the audio, and works out the tempo so the notes
can be written as sheet music. The result is MEDIA/piano/<id>/song.json; the app turns it
into an ABC piano song with the video attached.

Usage: pianovideo.py URL_OR_FILE [--media DIR]
Progress is printed as JSON lines, like karaoke.py.
"""
import argparse, json, os, re, shutil, sys, time
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from karaoke import W, H, say, frames, duration, audio, media_dir, download, video_id  # noqa: E402

KFPS = 30


def runs(mask):
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def find_keyboard(M):
    """Locate the black keys first, then the white-key separators just below them."""
    L = M.mean(2)
    rows = []
    for y in range(H // 3, H - 2):
        row = L[y]
        dark = [(a, b) for a, b in runs(row < 90) if 6 <= b - a <= 45]
        rows.append((y, dark, (row > 165).mean()))
    blk = [r for r in rows if 12 <= len(r[1]) <= 45 and r[2] > 0.3]
    if not blk:
        return None
    groups, cur = [], [blk[0]]
    for r in blk[1:]:
        if r[0] - cur[-1][0] <= 2:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    g = max(groups, key=lambda g: (len(g), g[0][0]))
    if len(g) < 15:
        return None
    ymid = g[len(g) * 2 // 3][0]
    ybot = g[-1][0]
    blacks = [((a + b) / 2, b - a) for a, b in g[len(g) * 2 // 3][1]]
    bx = [b[0] for b in blacks]
    expect = (bx[-1] - bx[0]) / max(1, len(bx) * 7 / 5 - 1)     # white key width implied by the black keys
    best = None
    for y in range(ybot + 3, min(H - 1, ybot + 40)):
        row = L[y]
        base = np.median(row)
        if base < 150:
            continue
        seps = [(a + b) / 2 for a, b in runs(row < base - 35) if b - a <= 5]
        if len(seps) < 8:
            continue
        d = np.diff(seps)
        md = np.median(d)
        ok = (np.abs(d - md) < 0.25 * md).mean()
        if abs(md - expect) > 0.3 * expect:
            continue
        sc = ok * len(seps)
        if best is None or sc > best[0]:
            best = (sc, y, seps, md)
    if not best:
        return None
    _, yw, seps, wkey = best
    # white keys: between separators, plus the partly visible ones at the edges
    edges = [s for s in seps]
    if edges[0] > 0.5 * wkey:
        edges = [max(0.0, edges[0] - wkey)] + edges
    if W - 1 - edges[-1] > 0.5 * wkey:
        edges = edges + [min(W - 1.0, edges[-1] + wkey)]
    whites = [((edges[i] + edges[i + 1]) / 2, edges[i], edges[i + 1]) for i in range(len(edges) - 1)
              if edges[i + 1] - edges[i] > 0.6 * wkey]
    # a white-key boundary with a black key on it; the gaps (E-F and B-C) fix the note names
    def has_black(x):
        return any(abs(b - x) < 0.35 * wkey for b in bx)
    bounds = [has_black(whites[i][2]) for i in range(len(whites) - 1)]
    # C is a white key whose left boundary has no black key and the next two boundaries have one
    best_off, best_sc = 0, -1
    pattern = [True, True, False, True, True, True, False]   # boundaries after C D E F G A B
    for off in range(7):      # off = index (mod 7) of the first white key in C D E F G A B order
        sc = sum(1 for i, b in enumerate(bounds) if pattern[(i + off) % 7] == b)
        if sc > best_sc:
            best_off, best_sc = off, sc
    steps = [0, 2, 4, 5, 7, 9, 11]
    keys = []
    for i, (xc, x0, x1) in enumerate(whites):
        deg = (i + best_off) % 7
        octave = (i + best_off) // 7
        keys.append(dict(x=xc, black=False, rel=octave * 12 + steps[deg], y=(ybot + yw) // 2))
        if i < len(whites) - 1 and pattern[deg] and has_black(x1):
            keys.append(dict(x=x1, black=True, rel=octave * 12 + steps[deg] + 1, y=ymid))
    return dict(keys=keys, wkey=wkey, ymid=ymid, ybot=ybot, yw=yw, pattern_fit=best_sc / max(1, len(bounds)))


def _scan(args):
    path, ss, t, keys = args
    xs = np.array([int(k['x']) for k in keys])
    ys = np.array([int(k['y']) for k in keys])
    out = []
    for tt, F in frames(path, KFPS, ss=ss, t=t):
        patch = np.mean([F[np.clip(ys + dy, 0, H - 1), np.clip(xs + dx, 0, W - 1)].astype(np.float32)
                         for dy in (-2, 0, 2) for dx in (-2, 0, 2)], axis=0)
        out.append((tt, patch.astype(np.uint8)))
    return out


def scan_keys(path, dur, keys, procs):
    for k in keys:
        k['y'] = int(k['y'])
    n = procs * 2
    L = dur / n
    rec = []
    with Pool(procs) as p:
        for i, part in enumerate(p.imap_unordered(_scan, [(path, j * L, L, keys) for j in range(n)])):
            rec += part
            say('notes', 0.1 + 0.6 * (i + 1) / n, 'Watching the keys')
    rec.sort(key=lambda r: r[0])
    return np.array([r[0] for r in rec]), np.stack([r[1] for r in rec]).astype(np.float32)


def presses(ts, C, keys):
    """Keys are 'down' while their colour is far from their usual colour."""
    idle = np.median(C, 0)
    dist = np.abs(C - idle[None]).sum(2)
    thr = np.array([70 if k['black'] else 90 for k in keys])
    lit = dist > thr[None]
    # title cards, fades and scene changes light up the whole keyboard at once: ignore those frames
    bad = lit.mean(1) > 0.3
    bad = np.convolve(bad, np.ones(5), 'same') > 0
    notes = []
    for k in range(C.shape[1]):
        on = lit[:, k] & ~bad
        # close one-frame gaps, drop one-frame blips
        for i in range(1, len(on) - 1):
            if not on[i] and on[i - 1] and on[i + 1]:
                on[i] = True
        for a, b in runs(on):
            if b - a < 2 or (ts[min(b, len(ts) - 1)] - ts[a]) > 12:
                continue
            col = C[a:b, k].mean(0)
            notes.append(dict(k=k, t0=float(ts[a]), t1=float(ts[min(b, len(ts) - 1)]), col=col))
    # a key held through a repeated note shows as one long press; split where the colour flickers back
    return notes


def hands(notes, keys):
    """Two lit colours usually mean two hands; otherwise split at middle C."""
    if not notes:
        return
    cols = np.array([n['col'] for n in notes])
    hue = np.arctan2(np.sqrt(3) * (cols[:, 1] - cols[:, 2]), 2 * cols[:, 0] - cols[:, 1] - cols[:, 2])
    c1, c2 = np.percentile(hue, 25), np.percentile(hue, 75)
    for _ in range(10):
        lab = np.abs(np.angle(np.exp(1j * (hue - c1)))) < np.abs(np.angle(np.exp(1j * (hue - c2))))
        if lab.all() or (~lab).all():
            break
        c1 = np.angle(np.exp(1j * hue[lab]).mean())
        c2 = np.angle(np.exp(1j * hue[~lab]).mean())
    sep = abs(np.angle(np.exp(1j * (c1 - c2))))
    rel = np.array([keys[n['k']]['rel'] for n in notes])
    if sep > 0.5 and 0.1 < lab.mean() < 0.9:
        rh_is_1 = rel[lab].mean() > rel[~lab].mean()
        for n, l in zip(notes, lab):
            n['hand'] = 'R' if l == rh_is_1 else 'L'
    else:
        mid = np.median(rel)
        for n in notes:
            n['hand'] = 'R' if keys[n['k']]['rel'] >= mid else 'L'


def octave_from_audio(path, notes, keys):
    """Try octave placements and keep the one whose fundamentals ring in the audio."""
    x, sr = audio(path)
    n = 8192
    best = None
    for base in range(12, 72, 12):         # MIDI number of the leftmost C-octave start
        score = 0.0
        for nt in notes[::max(1, len(notes) // 300)]:
            i0 = int((nt['t0'] + 0.03) * sr)
            seg = x[i0:i0 + n]
            if len(seg) < n:
                continue
            X = np.abs(np.fft.rfft(seg * np.hanning(n)))
            midi = base + keys[nt['k']]['rel']
            f = 440 * 2 ** ((midi - 69) / 12)
            b = int(round(f * n / sr))
            if b < 3 or b >= len(X) - 3:
                continue
            here = X[b - 2:b + 3].max()
            below = X[max(1, int(round(f / 2 * n / sr)) - 2):int(round(f / 2 * n / sr)) + 3].max()
            score += np.log1p(here) - 0.5 * np.log1p(below)
        if best is None or score > best[0]:
            best = (score, base)
    return best[1]


def tempo_and_grid(notes):
    """Estimate the tempo, then place beats and bar lines where most notes (and most left-hand notes) start."""
    t = np.array(sorted(n['t0'] for n in notes))
    if len(t) < 8:
        return 100.0, float(t[0]) if len(t) else 0.0
    best = None
    for bpm in np.arange(60, 181, 0.25):
        sub = 60 / bpm / 4
        z = np.exp(2j * np.pi * t / sub).sum()
        sc = abs(z) / len(t) - 0.0015 * abs(bpm - 100)
        if best is None or sc > best[0]:
            best = (sc, bpm, float(np.angle(z) / (2 * np.pi) * sub))
    _, bpm, ph = best
    # prefer the slower of two tempos that both fit (a 16th grid at 2x tempo fits just as well)
    beat = 60 / bpm
    sub = beat / 4
    lows = np.array(sorted(n['t0'] for n in notes if n.get('hand') == 'L')) if any(n.get('hand') == 'L' for n in notes) else t
    weights = {0: 1.0, 2: 0.5, 1: 0.15, 3: 0.15}
    best = None
    for k in range(16):                     # 16 sixteenth offsets inside a 4/4 bar
        start = ph + k * sub
        pos = np.round((t - start) / sub).astype(int) % 16
        sc = sum(weights[p % 4] for p in pos)
        lp = np.round((lows - start) / sub).astype(int) % 16
        sc += 1.5 * (lp == 0).sum() + 0.7 * (lp == 8).sum()
        if best is None or sc > best[0]:
            best = (sc, start)
    start = best[1]
    bar = 4 * beat
    start = start + np.floor((t[0] - start) / bar + 1e-6) * bar      # the bar that holds the first note
    return float(bpm), float(start)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source')
    ap.add_argument('--media')
    ap.add_argument('--procs', type=int, default=max(2, min(12, (os.cpu_count() or 4) // 2)))
    a = ap.parse_args()
    media = media_dir(a.media)
    sid = video_id(a.source)
    dest = os.path.join(media, 'piano', sid)
    os.makedirs(dest, exist_ok=True)
    T0 = time.time()
    try:
        if re.match(r'https?://', a.source):
            if os.path.exists(os.path.join(dest, 'video.mp4')):
                path, info = os.path.join(dest, 'video.mp4'), {}
                try:
                    old = json.load(open(os.path.join(dest, 'song.json'), encoding='utf-8'))
                    info = {'title': old.get('title'), 'uploader': old.get('artist')}
                except Exception:
                    pass
            else:
                say('download', 0, 'Downloading the video')
                path, info = download(a.source, dest, media)
        else:
            path = os.path.join(dest, 'video' + os.path.splitext(a.source)[1].lower())
            if os.path.abspath(a.source) != os.path.abspath(path):
                shutil.copyfile(a.source, path)
            info = {'title': os.path.splitext(os.path.basename(a.source))[0]}
        dur = duration(path)
        say('notes', 0.02, 'Looking for the keyboard')
        fr = [F for _, F in frames(path, 0.2, ss=min(5, dur * 0.05), t=max(5, dur - 10))]
        M = np.median(np.stack(fr), 0).astype(np.float32)
        kb = find_keyboard(M)
        if not kb or len(kb['keys']) < 20:
            raise RuntimeError('No piano keyboard was found at the bottom of this video.')
        keys = kb['keys']
        ts, C = scan_keys(path, dur, keys, a.procs)
        say('notes', 0.75, 'Finding the notes')
        notes = presses(ts, C, keys)
        if len(notes) < 10:
            raise RuntimeError('Hardly any key presses were seen. This works with falling-notes videos where keys light up.')
        hands(notes, keys)
        say('notes', 0.85, 'Checking the octave against the audio')
        base = octave_from_audio(path, notes, keys)
        bpm, start = tempo_and_grid(notes)
        beat = 60 / bpm
        out = []
        for n in notes:
            out.append([round(n['t0'], 3), round(n['t1'] - n['t0'], 3), int(base + keys[n['k']]['rel']), n['hand']])
        out.sort()
        song = {'version': 1, 'id': sid, 'title': info.get('title') or sid, 'artist': info.get('uploader') or '',
                'source': a.source if re.match(r'https?://', a.source) else '', 'video': os.path.basename(path),
                'youtube': sid if not sid.startswith('f-') else None, 'duration': round(dur, 2),
                'bpm': round(bpm, 2), 'start': round(start, 3), 'notes': out, 'created': int(time.time()),
                'analysis': {'keys': len(keys), 'white_width': round(kb['wkey'], 1), 'pattern_fit': round(kb['pattern_fit'], 3),
                             'lowest_midi': int(base + min(k['rel'] for k in keys))}}
        json.dump(song, open(os.path.join(dest, 'song.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        say('done', 1, f'{len(out)} notes at about {bpm:.0f} BPM, ready in {time.time() - T0:.0f} s')
    except Exception as e:
        say('error', None, str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
