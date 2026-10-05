#!/usr/bin/env python3
"""Turn a karaoke video (YouTube link or local file) into an Ongaku Renshuu karaoke song.

It downloads the video, reads the on-screen pitch bars into notes, finds the key from the
audio, times the lyric lines from their colour wipe, and (when Ollama is running) reads the
lyric text with a local vision model. Everything is written to MEDIA/karaoke/<id>/.

Usage: karaoke.py URL_OR_FILE [--media DIR] [--no-lyrics] [--ollama URL] [--model NAME]
Progress is printed as JSON lines: {"stage": ..., "progress": 0..1, "message": ...}
"""
import argparse, base64, io, json, os, re, shutil, subprocess, sys, time, urllib.request
from multiprocessing import Pool

import numpy as np

W, H = 1280, 720          # frames are analysed at this size
TB = 0.01                 # time resolution of the note map, seconds
FPS = 8                   # analysis frame rate
PAD = 14                  # rows kept above and below the staff


def say(stage, progress=None, message=''):
    print(json.dumps({'stage': stage, 'progress': progress, 'message': message}, ensure_ascii=False), flush=True)


# ---------------------------------------------------------------- video access
def frames(path, fps, ss=0.0, t=None):
    cmd = ['ffmpeg', '-loglevel', 'error', '-ss', f'{ss:.3f}', '-i', path]
    if t:
        cmd += ['-t', f'{t:.3f}']
    cmd += ['-vf', f'fps={fps},scale={W}:{H}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = W * H * 3
    i = 0
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield ss + i / fps, np.frombuffer(b, np.uint8).reshape(H, W, 3)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


def duration(path):
    out = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path])
    return float(out.decode().strip())


# ---------------------------------------------------------------- layout: staff lines and playhead
def thin_rows(F):
    g = F.astype(np.int16).sum(2)
    up = g[2:-2] - g[:-4]
    dn = g[2:-2] - g[4:]
    return np.pad((((up > 45) & (dn > 45)) | ((up < -45) & (dn < -45))).mean(1), (2, 2))


def find_staff(path, dur):
    """The pitch guide sits on evenly spaced long horizontal lines; return (top, bottom, spacing)."""
    acc, n = None, 0
    for _, F in frames(path, 0.5, ss=min(5, dur * 0.05), t=max(5, dur - 10)):
        r = thin_rows(F)
        acc = r if acc is None else acc + r
        n += 1
    if not n:
        return None
    acc /= n
    lines = []
    for y in [y for y in range(H // 2 + 60) if acc[y] > 0.25]:
        if lines and y - lines[-1][-1] <= 2:
            lines[-1].append(y)
        else:
            lines.append([y])
    c = [float(np.mean(l)) for l in lines]
    best = None
    for i in range(len(c)):
        for j in range(i + 3, len(c)):
            d = np.diff(c[i:j + 1])
            if d.mean() > 4 and d.std() < 0.15 * d.mean() and (best is None or j - i > best[1] - best[0]):
                best = (i, j, float(d.mean()))
    if not best:
        return None
    i, j, sp = best
    return c[i], c[j], sp


def vline(G):
    """Score each column for being a thin vertical line (the playhead)."""
    best = None
    for k in (3, 5, 8):
        a, l, r = G[:, k:-k], G[:, :-2 * k], G[:, 2 * k:]
        s = ((a - l > 40) & (a - r > 40)) | ((a - l < -40) & (a - r < -40))
        sc = np.pad(s.mean(0), (k, k))
        best = sc if best is None else np.maximum(best, sc)
    return best


# ---------------------------------------------------------------- bar detection
def bar_segments(R, hs, thr=50, bgthr=70, xs=2):
    """Find horizontal bars: a top and bottom edge h rows apart with opposite directions,
    filled with a colour unlike the row's background. Returns (ycenter, x0, x1, h)."""
    R = R[:, ::xs]
    G = np.zeros(R.shape, np.float32)
    G[2:] = R[2:] - R[:-2]
    mag = np.sqrt((G * G).sum(2))
    med = np.median(R, 1)
    Hh = R.shape[0]
    out = []
    for h in hs:
        mt, mb = mag[:Hh - h], mag[h:]
        cos = (G[:Hh - h] * G[h:]).sum(2) / (mt * mb + 1e-6)
        c = h // 2
        diff = np.abs(R[c:Hh - h + c] - med[c:Hh - h + c][:, None, :]).sum(2) > bgthr
        B = (mt > thr) & (mb > thr) & (cos < -0.5) & diff
        for a in np.nonzero(B.any(1))[0]:
            d = np.diff(np.r_[0, B[a].astype(np.int8), 0])
            st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
            i = 0
            while i < len(st):
                s, e = st[i], en[i]
                while i + 1 < len(st) and st[i + 1] - e <= 2:
                    i += 1
                    e = en[i]
                if (e - s) * xs >= 12:
                    out.append((a + h / 2, int(s * xs), int(e * xs), h))
                i += 1
    return out


def _detect_chunk(args):
    path, ss, t, y0, y1, hs = args
    rec, crops = [], []
    for i, (tt, F) in enumerate(frames(path, FPS, ss=ss, t=t)):
        R = F[y0 - PAD:y1 + PAD].astype(np.float32)
        sc = vline(F[y0:y1].astype(np.int16).sum(2))
        x = int(np.argmax(sc))
        segs = [(y + y0 - PAD, s, e, h) for (y, s, e, h) in bar_segments(R, hs)]
        rec.append((tt, x, float(sc[x]), segs))
        if i % 4 == 0:
            crops.append((tt, F[y0 - PAD:y1 + PAD, ::2].copy()))
    return rec, crops


def detect(path, dur, y0, y1, sp, procs):
    hs = list(range(max(5, round(0.45 * sp)), round(1.4 * sp) + 1))
    n = procs * 2
    L = dur / n
    jobs = [(path, i * L, L, y0, y1, hs) for i in range(n)]
    rec, crops = [], []
    with Pool(procs) as p:
        for k, (r, c) in enumerate(p.imap_unordered(_detect_chunk, jobs)):
            rec += r
            crops += c
            say('notes', 0.1 + 0.6 * (k + 1) / n, 'Reading the pitch bars')
    rec.sort(key=lambda r: r[0])
    crops.sort(key=lambda c: c[0])
    return rec, hs, crops


# ---------------------------------------------------------------- how bars move: scrolling or paged
def playhead_model(rec):
    xs = np.array([r[1] for r in rec])
    sc = np.array([r[2] for r in rec])
    ts = np.array([r[0] for r in rec])
    good = sc > 0.6
    vals, cnt = np.unique(xs[good] // 3, return_counts=True)
    if len(cnt) and cnt.max() > 0.3 * good.sum():
        return ('fixed', int(vals[cnt.argmax()] * 3 + 1))
    pts = [(t, x) for t, x, g in zip(ts, xs, sc > 0.5) if g]
    pages, cur = [], []
    for t, x in pts:
        if cur and (x < cur[-1][1] - 80 or t - cur[-1][0] > 2.0):
            pages.append(cur)
            cur = []
        cur.append((t, x))
    if cur:
        pages.append(cur)
    fits = []
    for pg in pages:
        if len(pg) < 3:
            continue
        T = np.array([p[0] for p in pg])
        X = np.array([p[1] for p in pg], float)
        keep = np.ones(len(T), bool)
        a = b = None
        for _ in range(3):
            if keep.sum() < 3:
                break
            b, a = np.polyfit(T[keep], X[keep], 1)
            keep = np.abs(X - (a + b * T)) < 20
        if b is None or keep.sum() < 3:
            continue
        rms = float(np.sqrt(np.mean((X[keep] - (a + b * T[keep])) ** 2)))
        fits.append([T[keep].min(), T[keep].max(), a, b, T[keep], X[keep], rms])
    if not fits:
        return ('paged', [])
    good = [f[3] for f in fits if f[6] < 10 and len(f[4]) >= 5 and f[3] > 30]
    med = float(np.median(good)) if good else float(np.median([f[3] for f in fits]))
    out = []
    for t0, t1, a, b, T, X, rms in fits:
        if not (rms < 10 and len(T) >= 5 and b > 30):   # few or messy points: assume the usual speed
            b = med
            a = float(np.median(X - b * T))
            keep = np.abs(X - (a + b * T)) < 25
            if keep.sum() < 2:
                continue
            t0, t1 = T[keep].min(), T[keep].max()
        out.append((float(t0), float(t1), float(a), float(b)))
    return ('paged', out)


def scroll_speed(rec):
    shifts = []
    for i in range(len(rec) - 1):
        byrow = {}
        for (y, s, e, h) in rec[i + 1][3]:
            byrow.setdefault((round(y), h), []).append((s, e))
        for (y, s, e, h) in rec[i][3]:
            for (s2, e2) in byrow.get((round(y), h), []):
                if abs((e - s) - (e2 - s2)) <= 4 and 2 < s - s2 < 250:
                    shifts.append(s - s2)
    if not shifts:
        return None
    hh, _ = np.histogram(shifts, bins=np.arange(2, 251))
    return (np.argmax(hh) + 2.5) * FPS


def tau_factory(model, speed):
    """Map a screen x in a frame at time t to the song time when the playhead reaches it."""
    if model[0] == 'fixed':
        px = model[1]
        return lambda t: (lambda X: t + (X - px) / speed)
    fits = model[1]

    def f(t):
        for (t0, t1, a, b) in fits:
            if t0 - 0.3 <= t <= t1 + 0.3:
                return lambda X, a=a, b=b: (X - a) / b
        return None
    return f


def drop_static(rec, y0, y1, limit=0.4):
    """Remove detections that sit at the same screen spot in most frames (staff gaps, decorations)."""
    Y0 = y0 - PAD
    NY = y1 + PAD - Y0
    O = np.zeros((NY, W // 2 + 1), np.float32)
    for (_, _, _, segs) in rec:
        for (y, s, e, h) in segs:
            yi = int(round(y)) - Y0
            if 0 <= yi < NY:
                O[yi, s // 2:e // 2 + 1] += 1
    O /= max(1, len(rec))
    O = np.maximum.reduce([O, np.roll(O, 1, 0), np.roll(O, -1, 0)])
    out = []
    for (t, x, sc, segs) in rec:
        keep = [(y, s, e, h) for (y, s, e, h) in segs
                if 0 <= int(round(y)) - Y0 < NY and (O[int(round(y)) - Y0, s // 2:e // 2 + 1] > limit).mean() < 0.5]
        out.append((t, x, sc, keep))
    return out


def coverage(rec, y0, y1, dur, tauf, maxlen=600):
    """For every row and song-time slot: the share of frames showing that slot that had a bar there."""
    nT = int((dur + 10) / TB)
    Y0 = y0 - PAD
    NY = y1 + PAD - Y0
    hits = np.zeros((NY, nT), np.float32)
    vis = np.zeros(nT, np.float32)
    for (t, x, sc, segs) in rec:
        tf = tauf(t)
        if tf is None:
            continue
        vis[max(0, int(tf(0) / TB)):min(nT, int(tf(W - 1) / TB))] += 1
        rows = {}
        for (y, s, e, h) in segs:
            if e - s > maxlen:
                continue
            yi = int(round(y)) - Y0
            for yy in (yi - 1, yi, yi + 1):
                if 0 <= yy < NY:
                    rows.setdefault(yy, []).append((s, e))
        for yi, iv in rows.items():
            iv.sort()
            cs, ce = iv[0]
            for s, e in iv[1:] + [(10 ** 9, 10 ** 9)]:
                if s > ce + 2:
                    hits[yi, max(0, int(tf(cs) / TB)):min(nT, int(tf(ce) / TB))] += 1
                    cs, ce = s, e
                else:
                    ce = max(ce, e)
    return np.clip(hits / np.maximum(vis, 1), 0, 1), Y0


def tsmooth(r, k=15):
    c = np.cumsum(np.pad(r, ((0, 0), (k // 2 + 1, k // 2)), mode='edge'), axis=1, dtype=np.float64)
    return ((c[:, k:] - c[:, :-k]) / k).astype(np.float32)


def trace(r, thr=0.5):
    rs = tsmooth(r)
    hot = r >= thr
    nT = r.shape[1]
    ys = np.full(nT, np.nan)
    for i in np.nonzero(hot.any(0))[0]:
        rows = np.nonzero(hot[:, i])[0]
        bands, cur = [], [rows[0]]
        for y in rows[1:]:
            if y - cur[-1] <= 2:
                cur.append(y)
            else:
                bands.append(cur)
                cur = [y]
        bands.append(cur)
        b = max(bands, key=lambda b: rs[b, i].sum())
        w = r[b, i]
        ys[i] = (np.array(b) * w).sum() / w.sum()
    i = 0
    while i < nT:   # bridge gaps under 50 ms when the height matches on both sides
        if np.isnan(ys[i]):
            j = i
            while j < nT and np.isnan(ys[j]):
                j += 1
            if 0 < i and j < nT and j - i <= 5 and abs(ys[i - 1] - ys[j]) < 2:
                ys[i:j] = (ys[i - 1] + ys[j]) / 2
            i = j
        else:
            i += 1
    return ys


def segment(ys, tol=4.0, mindur=0.05):
    notes = []
    i, n = 0, len(ys)
    while i < n:
        if np.isnan(ys[i]):
            i += 1
            continue
        j = i
        vals = [ys[i]]
        while j + 1 < n and not np.isnan(ys[j + 1]) and abs(ys[j + 1] - np.median(vals)) < tol:
            j += 1
            vals.append(ys[j])
        if (j - i + 1) * TB >= mindur:
            notes.append([i * TB, (j + 1) * TB, float(np.median(vals)), 0])
        i = j + 1
    return notes


def refine_y(notes, crops, tauf, Y0, sp):
    """Measure each note's height from saved frames, using the bar's top edge while it is still upcoming."""
    ts = np.array([c[0] for c in crops])
    for n in notes:
        a, b, yr = n[0], n[1], n[2]
        got = []
        order = [ci for ci in np.argsort(np.abs(ts - (a - 0.6)))[:60] if ts[ci] < a - 0.05]
        for ci in order:
            t, C = crops[ci]
            tf = tauf(t)
            if tf is None:
                continue
            ta, tb = tf(0), tf(W - 1)
            if a < ta or b > tb:
                continue
            xa = (a - ta) / (tb - ta) * (W - 1)
            xb = (b - ta) / (tb - ta) * (W - 1)
            m = (xb - xa) * 0.25
            ca, cb = int((xa + m) / 2), int((xb - m) / 2)
            if cb - ca < 3:
                ca = int((xa + xb) / 4) - 1
                cb = ca + 3
            Cf = C.astype(np.float32)
            prof = np.abs(np.median(Cf[:, ca:cb], 1) - np.median(Cf, 1)).sum(1)
            yi = int(round(yr)) - Y0
            if not 0 <= yi < len(prof) or prof[yi] < 60:
                continue
            top = bot = yi
            while top > 0 and prof[top - 1] >= 60:
                top -= 1
            while bot < len(prof) - 1 and prof[bot + 1] >= 60:
                bot += 1
            if bot - top > 3 * sp:
                continue
            got.append(Y0 + top)
            if len(got) >= 5:
                break
        if got:
            n[2] = float(np.median(got)) + sp / 2
        n[3] = len(got)
    return notes


# ---------------------------------------------------------------- paged guides: bars change colour as the line passes
def region_frames(path, ss, t, y0, y1, fps=12):
    hh = y1 - y0
    cmd = ['ffmpeg', '-loglevel', 'error', '-ss', f'{ss:.3f}', '-i', path, '-t', f'{t:.3f}',
           '-vf', f'fps={fps},scale={W}:{H},crop={W}:{hh}:0:{y0}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = W * hh * 3
    out = []
    i = 0
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        out.append((ss + i / fps, np.frombuffer(b, np.uint8).reshape(hh, W, 3).astype(np.int16)))
        i += 1
    p.wait()
    return out


def mask_bars(M, a, b, y0, sp):
    """Trace bars column by column: a bar continues while its run stays at the same height,
    and ends at a gap or a jump. Returns (start, end, centre y, height)."""
    H2, W2 = M.shape
    cols = []
    for x in range(W2):
        col = M[:, x]
        if not col.any():
            cols.append([])
            continue
        d = np.diff(np.r_[0, col.astype(np.int8), 0])
        runs = []
        for s0, e0 in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
            if runs and s0 - runs[-1][1] <= 4:
                runs[-1][1] = e0          # hollow bars: join the outline pieces
            else:
                runs.append([s0, e0])
        cols.append([((r0 + r1) / 2, r1 - r0) for r0, r1 in runs if 0.45 * sp <= r1 - r0 <= 2.0 * sp])
    live, done = [], []
    for x in range(W2):
        nxt, used = [], set()
        for bar in live:
            m = next((i for i, (yc, h) in enumerate(cols[x]) if i not in used and abs(yc - bar['yc']) <= 3), None)
            if m is None:
                bar['gap'] += 1
                (nxt if bar['gap'] <= 2 else done).append(bar)
                continue
            used.add(m)
            yc, h = cols[x][m]
            bar.update(x1=x, gap=0)
            bar['ys'].append(yc)
            bar['hs'].append(h)
            nxt.append(bar)
        for i, (yc, h) in enumerate(cols[x]):
            if i not in used:
                nxt.append(dict(x0=x, x1=x, yc=yc, gap=0, ys=[yc], hs=[h]))
        live = nxt
    out = []
    for bar in done + live:
        if bar['x1'] - bar['x0'] >= 10:
            out.append(((bar['x0'] - a) / b, (bar['x1'] + 1 - a) / b, y0 + float(np.median(bar['ys'])), float(np.median(bar['hs']))))
    return out


def _page_bars(args):
    """One page: for each column, compare a frame just before the line reaches it with one just after.
    Pixels that change then, and are steady before and after, belong to a bar (moving backgrounds are not steady)."""
    path, (t0, t1, a, b), y0, y1, sp = args
    fr = region_frames(path, max(0, t0 - 0.35), t1 - t0 + 0.7, y0, y1)
    if len(fr) < 4:
        return []
    ts = np.array([f[0] for f in fr])
    px = a + b * ts
    g = [0.0] + [float(np.abs(fr[q][1][::4, ::4] - fr[q - 1][1][::4, ::4]).mean()) for q in range(1, len(fr))]
    near = [q for q in range(1, len(fr)) if t0 - 0.35 <= ts[q] <= t0 + 0.1]
    lo = max(near, key=lambda q: g[q]) if near and max(g[q] for q in near) > 8 else int(np.argmin(np.abs(ts - (t0 - 0.08))))
    after = [q for q in range(lo + 1, len(fr)) if t1 - 0.1 <= ts[q] <= t1 + 0.35]
    hi = (max(after, key=lambda q: g[q]) - 1) if after and max(g[q] for q in after) > 8 else len(fr) - 1
    M = np.zeros((y1 - y0, W), bool)
    for x in range(0, W, 2):
        bi = np.nonzero(px < x - 14)[0]
        ai = np.nonzero(px > x + 14)[0]
        if not len(bi) or not len(ai):
            continue
        i, j = bi[-1], ai[0]
        if j - i > 6 or i < lo or j > hi:
            continue
        pi, k = max(lo, i - 3), min(hi, j + 3)
        d = np.abs(fr[j][1][:, x] - fr[i][1][:, x]).sum(1)
        pre = np.abs(fr[i][1][:, x] - fr[pi][1][:, x]).sum(1)
        post = np.abs(fr[k][1][:, x] - fr[j][1][:, x]).sum(1)
        M[:, x] = (d > 80) & (pre < 60) & (post < 60)
        M[:, min(W - 1, x + 1)] = M[:, x]
    return mask_bars(M, a, b, y0, sp)


def paged_notes(path, model, y0, y1, sp, procs):
    jobs = [(path, pg, y0 - PAD, y1 + PAD, sp) for pg in model[1]]
    bars = []
    with Pool(procs) as p:
        for k, r in enumerate(p.imap_unordered(_page_bars, jobs)):
            bars += r
            say('notes', 0.74 + 0.1 * (k + 1) / len(jobs), 'Reading each page of the pitch guide')
    bars.sort()
    out = []
    for t0, t1, y, h in bars:          # the same bar can appear on two neighbouring page fits
        if out and abs(out[-1][0] - t0) < 0.06 and abs(out[-1][2] - y) < 0.4 * sp:
            continue
        out.append([t0, t1, y, 1])
    return out


def grid_fit(notes, s):
    """Share of (duration-weighted) notes that sit on a grid of step s, and the grid's phase."""
    w = np.array([n[1] - n[0] for n in notes])
    y = np.array([n[2] for n in notes])
    best = (0.0, 0.0)
    for ph in np.arange(0, s, 0.25):
        d = np.abs(((y - ph) / s + 0.5) % 1 - 0.5)
        f = float(w[d < 0.22].sum() / w.sum())
        if f > best[0]:
            best = (f, ph)
    f, ph = best
    d = ((y - ph) / s + 0.5) % 1 - 0.5
    m = np.abs(d) < 0.22
    if m.any():
        ph = ph + float(np.average(d[m], weights=w[m])) * s
    return f, ph


def extract_notes(path, dur, procs):
    say('notes', 0.02, 'Looking for the pitch guide')
    st = find_staff(path, dur)
    if not st:
        raise RuntimeError('No pitch guide was found in this video. Karaoke videos with pitch bars (音程バー) work best.')
    y0, y1, sp = st
    y0, y1 = int(y0), int(y1) + 1
    rec, hs, crops = detect(path, dur, y0, y1, sp, procs)
    say('notes', 0.72, 'Working out the timing')
    model = playhead_model(rec)
    if model[0] == 'fixed':
        v = scroll_speed(rec)
        if not v:
            raise RuntimeError('The pitch bars did not seem to move, so they could not be timed.')
        best = None
        for f in np.linspace(0.94, 1.06, 25):
            r, _ = coverage(rec[::3], y0, y1, dur, tau_factory(model, v * f))
            s = float((r[r > 0.3] ** 2).sum())
            if best is None or s > best[0]:
                best = (s, v * f)
        speed = best[1]
    else:
        if not model[1]:
            raise RuntimeError('The playhead could not be followed in this video.')
        speed = None
    tauf = tau_factory(model, speed)
    notes = []
    if model[0] == 'paged':
        notes = [n for n in paged_notes(path, model, y0, y1, sp, procs) if n[1] - n[0] >= 0.05]
    if len(notes) < 10:      # scrolling guides, or a paged one whose bars do not change colour
        rec = drop_static(rec, y0, y1)
        say('notes', 0.8, 'Building the notes')
        r, Y0 = coverage(rec, y0, y1, dur, tauf)
        notes = segment(trace(r) + Y0)
        notes = refine_y(notes, crops, tauf, Y0, sp)
        notes = [n for n in notes if (n[3] > 0 and n[1] - n[0] >= 0.07) or n[1] - n[0] >= 0.15]
    if len(notes) < 10:
        raise RuntimeError('Too few notes were found in the pitch guide.')
    cands = [(grid_fit(notes, sp / k), sp / k) for k in (2, 1, 3)]
    (q, ph), s = cands[0] if cands[0][0][0] >= 0.6 else max(cands, key=lambda c: c[0][0])
    for n in notes:
        n.append(int(round((ph - n[2]) / s)))     # semitones, higher number is higher pitch
    info = {'layout': 'scrolling' if model[0] == 'fixed' else 'paged', 'staff': [y0, y1], 'semitone_px': round(s, 2),
            'on_grid': round(q, 3), 'speed': round(speed, 2) if speed else None}
    info['_model'] = model
    info['_bars'] = [(n[0], n[1], n[2], n[4]) for n in notes]    # time, end, screen y, semitone
    return [(n[0], n[1], n[4]) for n in notes], info, (y0, y1)


# ---------------------------------------------------------------- key from the audio
def audio(path, sr=22050):
    raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'])
    return np.frombuffer(raw, np.float32), sr


def pitch_salience(x, sr, n=8192, hop=1024):
    """Pitch-class strength per frame; each pitch collects its harmonics so a fifth does not outscore the root."""
    win = np.hanning(n).astype(np.float32)
    count = max(0, (len(x) - n) // hop)
    df = sr / n
    midis = np.arange(40, 90)
    wts = 0.8 ** np.arange(6)
    idx = np.array([np.round(440 * 2 ** ((m - 69) / 12) * np.arange(1, 7) / df).astype(int) for m in midis])
    C = np.zeros((count, 12), np.float32)
    for i in range(count):
        X = np.log1p(np.abs(np.fft.rfft(x[i * hop:i * hop + n] * win)))
        env = np.convolve(X, np.ones(31) / 31, 'same')
        X = np.maximum(0, X - env)
        Xm = np.maximum(np.maximum(X, np.roll(X, 1)), np.roll(X, -1))
        sal = (Xm[np.clip(idx, 0, len(X) - 1)] * wts).sum(1)
        C[i] = np.bincount(midis % 12, weights=sal, minlength=12)
    C /= C.sum(1, keepdims=True) + 1e-9
    return C, hop / sr


SOLFEGE = [('ファ', 5), ('ド', 0), ('レ', 2), ('ミ', 4), ('ソ', 7), ('ラ', 9), ('シ', 11)]


def read_label(text):
    """Turn a solfège label such as 'ファ#' or 'シ♭' into a pitch class."""
    t = text.strip().replace('＃', '#').replace('♯', '#').replace('♭', 'b')
    for name, pc in SOLFEGE:
        if t.startswith(name):
            rest = t[len(name):].strip()
            if rest.startswith('#'):
                pc += 1
            elif rest.startswith('b') or rest.startswith('フラット'):
                pc -= 1
            return pc % 12
    return None


def key_from_labels(path, info, url, model, want=9):
    """Paged guides (like カラオケ@DIVA) print a note name above each bar. Read a few with a local
    vision model; when they agree, they give the key exactly."""
    if info.get('layout') != 'paged':
        return None
    try:
        urllib.request.urlopen(url.rstrip('/') + '/api/tags', timeout=3).read()
    except Exception:
        return None
    from PIL import Image
    pages = info['_model'][1]
    sp = info['semitone_px'] * 2
    bars = [b for b in info['_bars'] if b[1] - b[0] >= 0.25]
    if len(bars) < 5:
        return None
    picks = [bars[i] for i in np.linspace(0, len(bars) - 1, min(want * 2, len(bars))).astype(int)]
    votes = {}
    asked = 0
    for (t, e, y, p) in picks:
        page = next((pg for pg in pages if pg[0] - 0.1 <= t <= pg[1]), None)
        if page is None:
            continue
        t0, t1, a, b = page
        x = a + b * t
        if t1 - t0 < 0.6:
            continue
        F = next((F for _, F in frames(path, 1, ss=(t0 + t1) / 2, t=0.5)), None)
        if F is None:
            continue
        ya, yb = int(max(0, y - 3.2 * sp)), int(max(0, y - 0.2 * sp))
        xa, xb = int(max(0, x - 12)), int(min(W, x + 60))
        crop = F[ya:yb, xa:xb]
        if crop.size == 0:
            continue
        im = Image.fromarray(crop).resize((crop.shape[1] * 3, crop.shape[0] * 3))
        buf = io.BytesIO()
        im.save(buf, 'PNG')
        prompt = ('This small image is cut from a karaoke pitch guide. It shows a note name written in katakana solfège '
                  '(ド, レ, ミ, ファ, ソ, ラ or シ), maybe followed by a sharp ♯ or flat ♭ sign. '
                  'Reply with only that note name, for example ファ♯. If there is no note name, reply none.')
        body = json.dumps({'model': model, 'stream': False, 'options': {'temperature': 0},
                           'messages': [{'role': 'user', 'content': prompt, 'images': [base64.b64encode(buf.getvalue()).decode()]}]}).encode()
        try:
            req = urllib.request.Request(url.rstrip('/') + '/api/chat', data=body, headers={'Content-Type': 'application/json'})
            text = (json.loads(urllib.request.urlopen(req, timeout=60).read()).get('message') or {}).get('content', '')
        except Exception:
            continue
        asked += 1
        pc = read_label(text)
        if os.environ.get('ONGAKU_DEBUG'):
            print(json.dumps({'debug': 'label', 't': round(t, 2), 'answer': text.strip()[:20], 'pc': pc, 'semitone': p}, ensure_ascii=False), file=sys.stderr)
        if pc is not None:
            k = (pc - p) % 12
            votes[k] = votes.get(k, 0) + 1
        say('key', 0.9, 'Reading the note names on the pitch guide')
        if asked >= want * 2 or (votes and max(votes.values()) >= want):
            break
    if not votes:
        return None
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    k, n = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if n >= 4 and n >= 2 * second and n >= 0.35 * sum(votes.values()):   # some misreads are normal; the right key clearly leads
        return k, n, sum(votes.values())
    return None


def fit_key(path, notes):
    x, sr = audio(path)
    C, dt = pitch_salience(x, sr)
    sc = np.zeros(12)
    for a, b, p in notes:
        i0 = int(a / dt)
        i1 = max(i0 + 1, int(b / dt))
        if i1 > len(C):
            continue
        c = C[i0:i1].mean(0)
        c = c - c.mean()
        for k in range(12):
            sc[k] += (b - a) * c[(p + k) % 12]
    order = np.argsort(sc)[::-1]
    conf = float(sc[order[0]] / max(1e-9, sc[order[1]])) if sc[order[1]] > 0 else 9.0
    return int(order[0]), conf


# ---------------------------------------------------------------- lyrics
def text_bands(F, ytop, ybot):
    R = F[ytop:ybot].astype(np.int16)
    E = np.abs(np.diff(R.sum(2), axis=1)) > 120
    rows = E.mean(1) > 0.025
    out, y = [], 0
    while y < len(rows):
        if rows[y]:
            a = y
            while y < len(rows) and (rows[y] or (y + 3 < len(rows) and rows[y + 1:y + 4].any())):
                y += 1
            if y - a >= 12:
                xs = np.nonzero(E[a:y].mean(0) > 0.03)[0]
                if len(xs) >= 20:
                    out.append([ytop + a, ytop + y, int(xs.min()), int(xs.max())])
        y += 1
    return out


def _lyric_chunk(args):
    path, ss, t, ytop = args
    out = []
    for tt, F in frames(path, 4, ss=ss, t=t):
        bands = text_bands(F, ytop, H - 10)
        if not bands:
            out.append((tt, []))
            continue
        hmax = max(b[1] - b[0] for b in bands)
        main = [b for b in bands if b[1] - b[0] >= max(24, 0.6 * hmax)]
        items = []
        for (a, b, x0, x1) in main:
            crop = F[a:b, x0:x1 + 1]
            g = crop.astype(np.int16).sum(2)
            edge = np.abs(np.diff(g, axis=1)) > 120
            # coarse signature: edge density in 4x16 cells, robust to the colour wipe
            hh, ww = edge.shape
            cells = np.zeros((4, 16), np.float32)
            for r in range(4):
                for c in range(16):
                    blk = edge[r * hh // 4:(r + 1) * hh // 4, c * ww // 16:(c + 1) * ww // 16]
                    cells[r, c] = blk.mean() if blk.size else 0
            # sung share: saturated (coloured) text pixels versus white/grey ones inside the strokes
            mx = crop.max(2).astype(np.int16)
            mn = crop.min(2).astype(np.int16)
            sat = (mx - mn) > 90
            bright = mx > 150
            textish = bright & ~((mx - mn) < 40) | (bright & (mx - mn) < 40)
            col_sat = (sat & bright).mean(0)
            col_txt = textish.mean(0)
            items.append(dict(box=[a, b, x0, x1], sig=cells.ravel(), colsat=col_sat.astype(np.float16), coltxt=col_txt.astype(np.float16)))
        out.append((tt, items))
    return out


def extract_lyric_lines(path, dur, ytop, procs):
    n = procs * 2
    L = dur / n
    jobs = [(path, i * L, L, ytop) for i in range(n)]
    rec = []
    with Pool(procs) as p:
        for k, part in enumerate(p.imap_unordered(_lyric_chunk, jobs)):
            rec += part
            say('lyrics', 0.05 + 0.35 * (k + 1) / n, 'Finding the lyric lines')
    rec.sort(key=lambda r: r[0])
    # track lines: same place on screen and a similar edge signature
    tracks, live = [], []
    for t, items in rec:
        nxt = []
        for it in items:
            a, b, x0, x1 = it['box']
            match = None
            for tr in live:
                A, B, X0, X1 = tr['box']
                if min(b, B) - max(a, A) > 0.6 * (b - a) and abs(x0 - X0) < 16 and abs(x1 - X1) < 24:
                    if np.corrcoef(tr['sig'], it['sig'])[0, 1] > 0.85:
                        match = tr
                        break
            if match is None:
                match = dict(box=it['box'], sig=it['sig'], t0=t, t1=t, samples=[], first=None)
                tracks.append(match)
            match['t1'] = t
            match['box'] = it['box']
            # how much of the line is coloured, measured left to right
            cs = it['colsat'].astype(np.float32)
            ct = it['coltxt'].astype(np.float32) + 1e-3
            share = cs / ct
            cols = ct > 0.02
            if cols.any():
                xs = np.nonzero(cols)[0]
                sung = xs[share[xs] > 0.35]
                frac = float((sung.max() - xs.min() + 1) / (xs.max() - xs.min() + 1)) if len(sung) else 0.0
                match['samples'].append((t, frac))
            nxt.append(match)
        live = nxt
    lines = []
    for tr in tracks:
        if tr['t1'] - tr['t0'] < 0.6:
            continue
        s = tr['samples']
        # the wipe only grows; fix the first reading as the baseline (some lines start coloured)
        base = s[0][1] if s else 0
        prog = [(t, max(0.0, min(1.0, (f - base) / max(0.05, 1 - base)))) for t, f in s]
        mono, m = [], 0.0
        for t, f in prog:
            m = max(m, f)
            mono.append((round(t, 2), round(m, 3)))
        start = next((t for t, f in mono if f > 0.03), None)
        end = next((t for t, f in mono if f > 0.95), None)
        lines.append(dict(t0=round(tr['t0'], 2), t1=round(tr['t1'] + 0.25, 2), s0=start, s1=end,
                          wipe=mono[::2], box=tr['box']))
    lines.sort(key=lambda l: (l['t0'], l['box'][0]))
    return lines


def ocr_lines(path, lines, url, model):
    """Read each lyric line once with a local vision model (Ollama). Lines that fail keep empty text."""
    try:
        urllib.request.urlopen(url.rstrip('/') + '/api/tags', timeout=3).read()
    except Exception:
        say('lyrics', 1, 'Ollama is not running, so the lyric text was skipped. The video still shows the lyrics.')
        return
    from PIL import Image
    for i, ln in enumerate(lines):
        t = (ln['t0'] + (ln['s0'] if ln['s0'] else ln['t0'])) / 2
        F = next((F for _, F in frames(path, 1, ss=max(0, t), t=0.6)), None)
        if F is None:
            continue
        a, b, x0, x1 = ln['box']
        h = b - a
        crop = F[max(0, a - int(0.5 * h)):min(H, b + 6), max(0, x0 - 10):min(W, x1 + 10)]
        buf = io.BytesIO()
        Image.fromarray(crop).save(buf, 'PNG')
        prompt = ('This image is one line of karaoke lyrics. Write out the main lyric text exactly as shown, '
                  'in its original script. Ignore the small furigana reading guides printed above the characters. '
                  'Reply with only the lyric text.')
        body = json.dumps({'model': model, 'stream': False, 'options': {'temperature': 0},
                           'messages': [{'role': 'user', 'content': prompt, 'images': [base64.b64encode(buf.getvalue()).decode()]}]}).encode()
        try:
            req = urllib.request.Request(url.rstrip('/') + '/api/chat', data=body, headers={'Content-Type': 'application/json'})
            res = json.loads(urllib.request.urlopen(req, timeout=120).read())
            text = (res.get('message') or {}).get('content', '').strip()
            text = re.sub(r'^["「『]|["」』]$', '', text.split('\n')[0]).strip()
            ln['text'] = text[:120]
        except Exception as e:
            ln['text'] = ''
        say('lyrics', 0.45 + 0.55 * (i + 1) / len(lines), 'Reading the lyrics with Ollama')


# ---------------------------------------------------------------- download and output
def media_dir(arg):
    d = arg or os.environ.get('ONGAKU_MEDIA') or os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'ongaku-renshuu-media')
    os.makedirs(os.path.join(d, 'karaoke'), exist_ok=True)
    return d


def ytdlp_cmd(media):
    """Prefer Ongaku Renshuu's own up-to-date copy of yt-dlp; YouTube often blocks older ones."""
    own = os.path.join(media, 'venv', 'bin', 'yt-dlp')
    if os.path.exists(own):
        return [own]
    if shutil.which('yt-dlp'):
        return ['yt-dlp']
    return [sys.executable, '-m', 'yt_dlp']


def update_ytdlp(media):
    say('download', None, 'Installing an up-to-date yt-dlp (once)')
    venv = os.path.join(media, 'venv')
    subprocess.check_call([sys.executable, '-m', 'venv', venv], stdout=subprocess.DEVNULL)
    subprocess.check_call([os.path.join(venv, 'bin', 'pip'), 'install', '-q', '-U', 'yt-dlp'], stdout=subprocess.DEVNULL)


def download(url, dest_dir, media):
    """Download as H.264 + AAC mp4 so every browser can play it. Returns (path, info)."""
    out = os.path.join(dest_dir, 'video.%(ext)s')
    fmt = 'bv*[vcodec^=avc1][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/bv*[height<=720]+ba/b'
    args = ['--no-playlist', '--no-warnings', '--newline', '-f', fmt, '--merge-output-format', 'mp4',
            '--write-info-json', '-o', out, '--', url]
    for attempt in (0, 1):
        p = subprocess.Popen(ytdlp_cmd(media) + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = []
        for line in p.stdout:
            log.append(line)
            m = re.search(r'\[download\]\s+([\d.]+)%', line)
            if m:
                say('download', float(m.group(1)) / 100, 'Downloading the video')
        p.wait()
        if p.returncode == 0:
            break
        if attempt == 0 and any('403' in l or 'Sign in' in l or 'Requested format' in l or 'nsig' in l for l in log):
            update_ytdlp(media)
            continue
        raise RuntimeError('The download failed: ' + ''.join(log[-3:]).strip())
    info = {}
    try:
        info = json.load(open(os.path.join(dest_dir, 'video.info.json')))
        os.remove(os.path.join(dest_dir, 'video.info.json'))
    except Exception:
        pass
    path = os.path.join(dest_dir, 'video.mp4')
    if not os.path.exists(path):
        cands = [f for f in os.listdir(dest_dir) if f.startswith('video.')]
        if not cands:
            raise RuntimeError('The download did not produce a video file.')
        path = os.path.join(dest_dir, cands[0])
    return path, info


def update_index(media):
    root = os.path.join(media, 'karaoke')
    items = []
    for d in sorted(os.listdir(root)):
        f = os.path.join(root, d, 'song.json')
        try:
            s = json.load(open(f, encoding='utf-8'))
            items.append({k: s.get(k) for k in ('id', 'title', 'artist', 'duration', 'created', 'notes_count', 'lyrics_count', 'source')})
        except Exception:
            pass
    tmp = os.path.join(root, 'index.json.tmp')
    json.dump(items, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, os.path.join(root, 'index.json'))


def video_id(src):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})', src)
    if m:
        return m.group(1)
    base = re.sub(r'[^\w-]+', '-', os.path.splitext(os.path.basename(src))[0]).strip('-')[:40] or 'video'
    return 'f-' + base


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('source')
    ap.add_argument('--media')
    ap.add_argument('--no-lyrics', action='store_true')
    ap.add_argument('--ollama', default=os.environ.get('OLLAMA_HOST', 'http://127.0.0.1:11434'))
    ap.add_argument('--model', default='qwen2.5vl:7b')
    ap.add_argument('--procs', type=int, default=max(2, min(12, (os.cpu_count() or 4) // 2)))
    a = ap.parse_args()
    if not a.ollama.startswith('http'):
        a.ollama = 'http://' + a.ollama
    media = media_dir(a.media)
    sid = video_id(a.source)
    dest = os.path.join(media, 'karaoke', sid)
    os.makedirs(dest, exist_ok=True)
    T0 = time.time()
    try:
        if re.match(r'https?://', a.source):
            if os.path.exists(os.path.join(dest, 'video.mp4')):
                path, info = os.path.join(dest, 'video.mp4'), {}
                try:
                    old = json.load(open(os.path.join(dest, 'song.json'), encoding='utf-8'))
                    info = old.get('source_info') or {'title': old.get('title'), 'uploader': old.get('artist')}
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
        song = {'version': 1, 'id': sid, 'title': info.get('title') or sid, 'artist': info.get('uploader') or '',
                'source': a.source if re.match(r'https?://', a.source) else '', 'duration': round(dur, 2),
                'video': os.path.basename(path), 'created': int(time.time()), 'notes': [], 'lyrics': [],
                'source_info': {k: info.get(k) for k in ('title', 'uploader', 'webpage_url') if info.get(k)}}
        notes, ninfo, (y0, y1) = extract_notes(path, dur, a.procs)
        say('key', 0.9, 'Finding the key from the audio')
        k, conf = fit_key(path, notes)
        song['key_source'] = 'audio'
        lab = key_from_labels(path, ninfo, a.ollama, a.model)
        if lab:
            if lab[0] != k:
                say('key', 0.95, 'The note names on the guide corrected the key')
            k, conf = lab[0], 9.0
            song['key_source'] = f'note names ({lab[1]} of {lab[2]} agree)'
        ninfo.pop('_model', None)
        ninfo.pop('_bars', None)
        # place the melody in a comfortable octave: middle of the song around D4
        med = float(np.median([p + k for _, _, p in notes]))
        octave = int(round((62 - med) / 12)) * 12
        song['notes'] = [[round(s, 3), round(e - s, 3), int(p + k + octave)] for s, e, p in notes]
        song['key_confidence'] = round(conf, 2)
        song['analysis'] = ninfo
        song['notes_count'] = len(notes)
        json.dump(song, open(os.path.join(dest, 'song.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        update_index(media)
        say('notes', 1, f'Pitch guide ready: {len(notes)} notes')
        if not a.no_lyrics:
            lines = extract_lyric_lines(path, dur, min(H - 120, y1 + 24), a.procs)
            for ln in lines:
                ln['text'] = ''
            song['lyrics'] = lines
            song['lyrics_count'] = len(lines)
            json.dump(song, open(os.path.join(dest, 'song.json'), 'w', encoding='utf-8'), ensure_ascii=False)
            if lines:
                ocr_lines(path, lines, a.ollama, a.model)
            for ln in lines:
                ln['y'] = ln['box'][0]
                ln.pop('box', None)
            json.dump(song, open(os.path.join(dest, 'song.json'), 'w', encoding='utf-8'), ensure_ascii=False)
        update_index(media)
        say('done', 1, f'Ready in {time.time() - T0:.0f} s')
    except Exception as e:
        say('error', None, str(e))
        sys.exit(1)


if __name__ == '__main__':
    main()
