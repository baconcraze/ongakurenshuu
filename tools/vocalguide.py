#!/usr/bin/env python3
"""Build a karaoke pitch guide from the original recording's vocals.

The karaoke track itself has no lead vocal, so this finds the original song on YouTube (or takes
the link you give it), lines it up with the karaoke audio, separates the vocal with Demucs, follows
the sung pitch, and turns it into notes on the karaoke video's timeline.

Used by karaoke.py, which falls back to reading the video's own pitch bars when this fails.
Run on its own for testing: vocalguide.py KARAOKE_MEDIA_FILE --title TITLE [--original URL]
"""
import json, os, re, shutil, subprocess, sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000            # pitch tracking rate
HOP = 160             # 10 ms frames
CSR = 11025           # matching rate
CHOP = 1102           # about 0.1 s matching frames


def say(stage, progress=None, message=''):
    print(json.dumps({'stage': stage, 'progress': progress, 'message': message}, ensure_ascii=False), flush=True)


def load_audio(path, sr):
    raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'])
    return np.frombuffer(raw, np.float32).copy()


# ---------------------------------------------------------------- finding the original
JUNK = r'(カラオケ|karaoke|ガイドなし|ガイド無し|ガイドあり|ガイドメロディ|guide|off\s*vocal|オフボーカル|instrumental|inst\.?|' \
       r'歌詞付き?|lyrics?|full|フル|ver\.?|version|hd|4k|mv|pv|official|公式|音程バー|歌ってみた|cover|カバー|on\s*vocal|' \
       r'原曲キー|キー変更|[+-]\d+key|key\s*[+-]?\d+|高音質|字幕)'


def search_query(title):
    """Strip karaoke words and bracketed tags from a karaoke video's title."""
    t = re.sub(r'[【\[［(（「『<〈《].*?[】\]］)）」』>〉》]', ' ', title or '')
    t = re.sub(JUNK, ' ', t, flags=re.I)
    t = re.sub(r'[/／|｜:：~〜\-–—_・,、]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t or (title or '').strip()


BAD = re.compile(r'カラオケ|karaoke|instrumental|off\s*vocal|オフボーカル|歌ってみた|cover|カバー|piano|ピアノ|guitar|ギター|'
                 r'orchestra|オーケストラ|remix|リミックス|弾いてみた|叩いてみた|吹いてみた|演奏|music\s*box|オルゴール|8.?bit|'
                 r'nightcore|slowed|sped|trumpet|トランペット|sax|サックス|flute|フルート|violin|バイオリン|ヴァイオリン|'
                 r'cello|チェロ|ocarina|オカリナ|harmonica|ハーモニカ|erhu|二胡|ukulele|ウクレレ|tutorial|lesson|レッスン|'
                 r'midi|synthesia|reaction|リアクション|踊ってみた|dance', re.I)
LIVE = re.compile(r'live|ライブ|concert|コンサート|tour|ツアー', re.I)
GOOD = re.compile(r'official|公式|- topic|provided to youtube|audio|オフィシャル', re.I)


def find_candidates(ytdlp, title, duration, want=4, exclude=()):
    """Search YouTube for the original recording; rank by how close the length is and how official it looks."""
    q = search_query(title)
    seen, cands = set(), []
    for query in (q, q + ' official audio'):
        try:
            out = subprocess.run(ytdlp + ['--no-warnings', '--flat-playlist', '-J', f'ytsearch10:{query}'],
                                 capture_output=True, text=True, timeout=90).stdout
            entries = (json.loads(out) or {}).get('entries') or []
        except Exception:
            entries = []
        for e in entries:
            vid, d = e.get('id'), e.get('duration')
            if not vid or vid in seen or not d or vid in exclude:
                continue
            seen.add(vid)
            name = f"{e.get('title') or ''} {e.get('channel') or e.get('uploader') or ''}"
            if BAD.search(name):
                continue
            cost = abs(d - duration) / max(30.0, duration) * 10
            if abs(d - duration) > max(25, 0.15 * duration):
                cost += 6
            if LIVE.search(name):
                cost += 2.5
            if GOOD.search(name):
                cost -= 1
            cands.append((cost, {'id': vid, 'title': e.get('title') or vid, 'channel': e.get('channel') or e.get('uploader') or '',
                                 'duration': d, 'url': f'https://www.youtube.com/watch?v={vid}'}))
    cands.sort(key=lambda c: c[0])
    return q, [c[1] for c in cands[:want]]


def download_audio(ytdlp, url, dest):
    """Audio only, saved as dest.m4a (or whatever YouTube offers)."""
    base = os.path.splitext(dest)[0]
    for f in os.listdir(os.path.dirname(dest) or '.'):
        if f.startswith(os.path.basename(base) + '.'):
            return os.path.join(os.path.dirname(dest), f)
    for fmt in ('ba[ext=m4a]/ba', 'ba*', 'b'):
        p = subprocess.run(ytdlp + ['--no-playlist', '--no-warnings', '-q', '-f', fmt, '-o', base + '.%(ext)s', '--', url],
                           capture_output=True, text=True, timeout=600)
        got = [f for f in os.listdir(os.path.dirname(dest) or '.') if f.startswith(os.path.basename(base) + '.') and not f.endswith('.part')]
        if p.returncode == 0 and got:
            return os.path.join(os.path.dirname(dest), got[0])
    raise RuntimeError('could not download the original song: ' + (p.stderr.strip().splitlines() or [''])[-1])


# ---------------------------------------------------------------- lining up the original with the karaoke track
def chroma(x, sr=CSR, n=4096, hop=CHOP, smooth=5):
    """12 pitch classes per 0.1 s frame (80 Hz to 1.8 kHz), lightly smoothed, each frame normalised.
    Every class averages its own FFT bins, so no class is favoured just by having more bins."""
    win = np.hanning(n).astype(np.float32)
    count = max(1, (len(x) - n) // hop)
    f = np.fft.rfftfreq(n, 1 / sr)
    band = (f > 80) & (f < 1800)
    pc = (np.round(12 * np.log2(f[band] / 440)) % 12).astype(int)
    M = np.zeros((12, band.sum()), np.float32)
    M[pc, np.arange(band.sum())] = 1
    M /= M.sum(1, keepdims=True)
    out = np.zeros((count, 12), np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, n)
    for i0 in range(0, count, 512):
        idx = np.arange(i0, min(count, i0 + 512))
        S = np.abs(np.fft.rfft(view[idx * hop] * win, axis=1))[:, band]
        S = np.log1p(100 * S / (S.max(1, keepdims=True) + 1e-9))
        out[idx] = S @ M.T
    k = np.ones(smooth) / smooth
    out = np.apply_along_axis(lambda c: np.convolve(c, k, 'same'), 0, out)
    out -= out.mean(1, keepdims=True)
    out /= np.linalg.norm(out, axis=1, keepdims=True) + 1e-6
    return out


def xcorr(a, b):
    """Correlation of a against b for every lag (b shifted later by lag)."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    r = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    return np.concatenate([r[-(len(b) - 1):], r[:len(a)]]), len(b) - 1


def global_match(Ck, Co, max_lag=90.0):
    """Best single offset and key shift. Returns (lag frames, shift, correlation, gain over chance)."""
    L = int(max_lag / (CHOP / CSR))
    best = None
    for k in range(-6, 6):
        Cs = np.roll(Co, k, axis=1)
        r, zero = xcorr(Ck[:, 0], Cs[:, 0])
        for i in range(1, 12):
            r = r + xcorr(Ck[:, i], Cs[:, i])[0]
        lags = np.arange(len(r)) - zero
        ov = np.minimum(len(Ck), len(Cs) + lags) - np.maximum(0, lags)
        r = r / np.maximum(ov, len(Ck) * 0.4)
        i = int(np.argmax(np.where(np.abs(lags) <= L, r, -9)))
        if best is None or r[i] > best[0]:
            best = (float(r[i]), int(lags[i]), k)
    corr, lag, shift = best
    rng = np.random.default_rng(1)
    Cs = np.roll(Co, shift, axis=1)
    chance = float((Ck[rng.integers(0, len(Ck), 4000)] * Cs[rng.integers(0, len(Cs), 4000)]).sum(1).mean())
    return lag, shift, corr, corr - chance


def warp(Ck, Co, lag, band_s=10.0):
    """Dynamic time warping inside a band around the global offset, so tempo drift and sections of a
    different length in the karaoke arrangement are followed. Steps (1,1), (1,2), (2,1) keep each row
    depending only on earlier rows, which lets numpy do a whole row at once.
    Returns the path as arrays of (karaoke frame, original frame)."""
    nI, nJ = len(Ck), len(Co)
    band = int(band_s / (CHOP / CSR))
    W = 2 * band + 1
    INF = 1e9
    cols = lambda i: i - lag - band + np.arange(W)
    D = np.full((nI, W), INF)
    P = np.zeros((nI, W), np.int8)
    for i in range(nI):
        j = cols(i)
        ok = (j >= 0) & (j < nJ)
        cost = np.full(W, INF)
        cost[ok] = 1 - (Ck[i] * Co[j[ok]]).sum(1)
        start = cost if i < band or (j[ok].size and j[ok].min() < band) else np.full(W, INF)
        if i == 0:
            D[i] = cost
            P[i] = 3
            continue
        a = D[i - 1]
        b = np.r_[INF, D[i - 1][:-1]]
        c = np.r_[D[i - 2][1:], INF] if i >= 2 else np.full(W, INF)
        st = np.vstack([a, b, c])
        k = st.argmin(0)
        best = st[k, np.arange(W)] + cost
        free = start < best
        D[i] = np.where(free, start, best)
        P[i] = np.where(free, 3, k)
    # the path may end anywhere near the end of either recording
    ends = [(D[i, w] / (i + 1), i, w) for i in range(max(0, nI - band), nI) for w in range(W)
            if D[i, w] < INF and (i == nI - 1 or cols(i)[w] >= nJ - 2)]
    if not ends:      # the recordings end far apart: take the best point near the end of either one
        fin = np.argwhere(D < INF)
        near = [(i, w) for i, w in fin if i >= nI - 2 * band or cols(i)[w] >= nJ - 2 * band]
        ends = [(D[i, w] / (i + 1), i, w) for i, w in near]
    if not ends:
        raise RuntimeError('the recordings could not be lined up')
    _, i, w = min(ends)
    path = []
    while i >= 0 and 0 <= w < W:
        path.append((i, int(cols(i)[w])))
        k = P[i, w]
        if k == 3:
            break
        if k == 0:
            i -= 1
        elif k == 1:
            i, w = i - 1, w - 1
        else:
            i, w = i - 2, w + 1
    path = np.array(path[::-1])
    return path[:, 0], path[:, 1]


def time_map(ki, oj, smooth_s=12.0):
    """Turn the warping path into a smooth original-time to karaoke-time map (seconds), robust to the
    path's frame-to-frame jitter: the offset is median-filtered along the original's timeline."""
    dt = CHOP / CSR
    to = oj * dt
    off = (ki - oj) * dt
    grid = np.arange(0, to.max() + dt, dt)
    o = np.interp(grid, to, off)
    k = int(smooth_s / dt) | 1
    if len(o) > k:
        o = medfilt(o, k)
        m = (k // 2) | 1
        o = np.convolve(np.pad(o, m // 2, mode='edge'), np.ones(m) / m, 'valid')
    tk = np.maximum.accumulate(grid + o)
    return grid, tk


def align(kara, orig):
    """Line the original up with the karaoke track.
    Returns (original times, karaoke times) for mapping, key shift (karaoke = original + shift),
    correlation, and gain over chance (about 0.2 for the same song, under 0.1 for others)."""
    Ck, Co0 = chroma(kara), chroma(orig)
    lag, shift, corr, gain = global_match(Ck, Co0)
    Co = np.roll(Co0, shift, axis=1)
    ki, oj = warp(Ck, Co, lag)
    to, tk = time_map(ki, oj)
    return to, tk, shift, corr, gain


# ---------------------------------------------------------------- the karaoke video's lyric wipe
WFPS = 12.5           # wipe analysis frame rate
WW, WH = 480, 270     # wipe analysis size


def _wipe_chunk(args):
    """Count pixels that change into or out of a strong colour and then stay put: the lyric wipe
    (white text turning colour, or a coloured outline appearing). Moving backgrounds rarely stay put."""
    path, k0, k1 = args
    ss = max(0, k0 - 8) / WFPS
    cmd = ['ffmpeg', '-loglevel', 'error', '-ss', f'{ss:.3f}', '-i', path, '-t', f'{(k1 - k0 + 9) / WFPS:.3f}',
           '-vf', f'fps={WFPS},scale={WW}:{WH}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    n = WW * WH * 3
    buf, out = [], {}
    k = max(0, k0 - 8)
    while True:
        b = p.stdout.read(n)
        if len(b) < n:
            break
        F = np.frombuffer(b, np.uint8).reshape(WH, WW, 3).astype(np.int16)
        buf = (buf + [(F, (F.max(2) - F.min(2)) > 70)])[-9:]
        if len(buf) == 9:
            a, (x0, s0), (x1, s1), z = buf[0][0], buf[2], buf[4], buf[8][0]
            m = (np.abs(x1 - x0).sum(2) > 120) & (np.abs(z - x1).sum(2) < 60) & (np.abs(x0 - a).sum(2) < 60) & (s0 | s1)
            c = k - 5          # the change happened between frames k-6 and k-4
            if k0 <= c < k1:
                out[c] = int(m.sum())
        k += 1
    p.wait()
    return out


def wipe_activity(path, duration, procs):
    from multiprocessing import Pool
    total = int(duration * WFPS) + 1
    n = max(1, procs * 2)
    edges = np.linspace(0, total, n + 1).astype(int)
    act = np.zeros(total, np.float32)
    with Pool(procs) as pool:
        for k, part in enumerate(pool.imap_unordered(_wipe_chunk, [(path, int(a), int(b)) for a, b in zip(edges, edges[1:])])):
            for i, val in part.items():
                act[i] = val
            say('align', 0.78 + 0.1 * (k + 1) / n, 'Reading the lyric wipe to fine-tune the timing')
    return act


def wipe_onsets(act, quiet=5):
    """Times where the wipe starts moving after a pause: the start of a lyric line."""
    nz = act[act > 0]
    if len(nz) < 20:
        return np.array([])
    q = act > max(20, 0.03 * np.percentile(nz, 90))
    return np.array([k / WFPS for k in range(quiet, len(q)) if q[k] and not q[k - quiet:k].any()]) + 0.08


def snap_phrases(notes, K0, onsets, radius=0.8, gap=0.35):
    """Move each sung phrase so it starts on a lyric line start. The correction found for one phrase is the
    first guess for the next (the coarse alignment's error changes slowly), and phrases with no line start
    nearby keep that running correction. Returns (original time -> karaoke time, phrases snapped, phrases)."""
    starts = [0] + [i + 1 for i, (n0, n1) in enumerate(zip(notes, notes[1:])) if n1[0] - n0[1] > gap]
    pt, sh = [], []
    snapped, last_end, d = 0, -1e9, 0.0
    for pi, s in enumerate(starts):
        e = starts[pi + 1] if pi + 1 < len(starts) else len(notes)
        t = K0(notes[s][0])
        if len(onsets):
            near = onsets[np.abs(onsets - (t + d)) < radius]
            near = near[near > last_end + 0.05]          # never overlap the phrase before
            if len(near):
                d = float(near[np.argmin(np.abs(near - (t + d)))]) - t
                snapped += 1
            else:
                d *= 0.7                                  # drift back toward the coarse alignment
        pt.append(notes[s][0] - 0.05)
        sh.append(d)
        last_end = K0(notes[e - 1][1]) + d
    pt, sh = np.array(pt), np.array(sh)

    def K(t):
        i = int(np.searchsorted(pt, t, 'right')) - 1
        return K0(t) + (sh[i] if i >= 0 else 0.0)
    return K, snapped, len(starts)


# ---------------------------------------------------------------- separating the vocal
def venv_dir(media):
    return os.path.join(media, 'vocal-venv')


def separator_ready(media):
    py = os.path.join(venv_dir(media), 'bin', 'python')
    if not os.path.exists(py):
        return False
    return subprocess.run([py, '-c', 'import demucs.pretrained, torch'], capture_output=True).returncode == 0


def install_separator(media):
    """PyTorch and Demucs in their own folder, once (a few GB)."""
    v = venv_dir(media)
    say('vocals', None, 'Installing the vocal separator (once, a few GB). This can take a while.')
    if not os.path.exists(os.path.join(v, 'bin', 'python')):
        subprocess.check_call([sys.executable, '-m', 'venv', v], stdout=subprocess.DEVNULL)
    pip = [os.path.join(v, 'bin', 'python'), '-m', 'pip', 'install', '-q']
    subprocess.check_call(pip + ['-U', 'pip'], stdout=subprocess.DEVNULL)
    subprocess.check_call(pip + ['torch', 'torchaudio'], stdout=subprocess.DEVNULL)
    subprocess.check_call(pip + ['numpy', 'demucs'], stdout=subprocess.DEVNULL)


def separate(media, src, out_wav):
    """Run separate.py in the separator's own Python. It writes the vocal stem as 16 kHz mono wav."""
    if os.path.exists(out_wav):
        return out_wav
    if not separator_ready(media):
        install_separator(media)
    py = os.path.join(venv_dir(media), 'bin', 'python')
    p = subprocess.Popen([py, os.path.join(HERE, 'separate.py'), src, out_wav], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    tail = []
    for line in p.stdout:
        tail = (tail + [line.strip()])[-5:]
        m = re.match(r'progress ([\d.]+)', line)
        if m:
            say('vocals', 0.35 + 0.35 * float(m.group(1)), 'Separating the vocal from the original song')
    p.wait()
    if p.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError('the vocal could not be separated: ' + ' '.join(tail[-2:]))
    return out_wav


# ---------------------------------------------------------------- following the sung pitch
def yin_track(x, sr=SR, hop=HOP, win=640, fmin=60.0, fmax=1000.0, thr=0.18):
    """YIN pitch per 10 ms frame. Returns midi (nan when unvoiced), aperiodicity, and rms."""
    tmax = int(sr / fmin)
    tmin = int(sr / fmax)
    n = win + tmax
    count = max(1, (len(x) - n) // hop)
    nfft = 1 << int(np.ceil(np.log2(n + win)))
    midi = np.full(count, np.nan, np.float32)
    ap = np.ones(count, np.float32)
    rms = np.zeros(count, np.float32)
    view = np.lib.stride_tricks.sliding_window_view(x, n)
    taus = np.arange(tmax + 1)
    for i0 in range(0, count, 1024):
        idx = np.arange(i0, min(count, i0 + 1024))
        fr = view[idx * hop].astype(np.float64)
        head = fr[:, :win]
        r = np.fft.irfft(np.fft.rfft(fr, nfft) * np.conj(np.fft.rfft(head, nfft)), nfft)[:, :tmax + 1]
        cs = np.concatenate([np.zeros((len(idx), 1)), np.cumsum(fr * fr, 1)], 1)
        e0 = cs[:, win][:, None]
        et = cs[:, taus + win] - cs[:, taus]
        d = np.maximum(e0 + et - 2 * r, 0)
        d[:, 0] = 1
        cm = d[:, 1:] * np.arange(1, tmax + 1) / (np.cumsum(d[:, 1:], 1) + 1e-12)
        cm = np.concatenate([np.ones((len(idx), 1)), cm], 1)
        rms[idx] = np.sqrt(e0[:, 0] / win)
        below = cm[:, tmin:] < thr
        has = below.any(1)
        first = np.where(has, below.argmax(1) + tmin, np.argmin(cm[:, tmin:], 1) + tmin)
        # walk down to the local minimum after the threshold crossing
        for _ in range(40):
            nxt = np.minimum(first + 1, tmax)
            step = cm[np.arange(len(idx)), nxt] < cm[np.arange(len(idx)), first]
            if not step.any():
                break
            first = np.where(step, nxt, first)
        t = np.clip(first, 1, tmax - 1)
        a, b, c = (cm[np.arange(len(idx)), t + o] for o in (-1, 0, 1))
        den = a - 2 * b + c
        p = t + np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(den == 0, 1, den), 0)
        ap[idx] = b
        midi[idx] = 69 + 12 * np.log2(sr / p / 440)
    return midi, ap, rms


def medfilt(v, k):
    pad = k // 2
    w = np.lib.stride_tricks.sliding_window_view(np.pad(v, pad, mode='edge'), k)
    with np.errstate(all='ignore'), __import__('warnings').catch_warnings():
        __import__('warnings').simplefilter('ignore', RuntimeWarning)
        return np.nanmedian(w, 1)


def clean_pitch(midi, ap, rms):
    """Keep confident, loud-enough frames; fix octave slips; smooth lightly."""
    loud = np.percentile(rms[rms > 0], 95) if (rms > 0).any() else 1
    voiced = (ap < 0.3) & (rms > loud * 0.06) & np.isfinite(midi)
    m = np.where(voiced, midi, np.nan)
    # octave slips: a short run an octave away from its neighbourhood gets folded back
    ref = medfilt(m, 51)
    for k in (12, -12):
        slip = np.isfinite(ref) & (np.abs(m - ref - k) < 2.5)
        m[slip] -= k
    m = medfilt(m, 5)
    m[~voiced] = np.nan
    # drop islands shorter than 60 ms
    v = np.isfinite(m)
    d = np.diff(np.r_[0, v.astype(np.int8), 0])
    for s, e in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
        if e - s < 6:
            m[s:e] = np.nan
    return m


def tuning_offset(m):
    """How far the recording sits from A440, in semitones (-0.5..0.5)."""
    v = m[np.isfinite(m)]
    if len(v) < 50:
        return 0.0
    ang = 2 * np.pi * (v - np.round(v))
    return float(np.angle(np.exp(1j * ang).mean()) / (2 * np.pi))


def to_notes(m, dt=HOP / SR, tol=0.75, mindur=0.09):
    """Split the pitch line into notes: a note holds while the pitch stays near its running median."""
    notes, i, n = [], 0, len(m)
    while i < n:
        if not np.isfinite(m[i]):
            i += 1
            continue
        j = i
        while j + 1 < n and np.isfinite(m[j + 1]) and abs(m[j + 1] - np.median(m[max(i, j - 15):j + 1])) < tol:
            j += 1
        seg = m[i:j + 1]
        if (j - i + 1) * dt >= mindur:
            notes.append([i * dt, (j + 1) * dt, float(np.median(seg))])
        i = j + 1
    out = []
    for s, e, p in notes:   # join repeats of one pitch split by a tiny gap (a breath of vibrato)
        q = int(round(p))
        if out and out[-1][2] == q and s - out[-1][1] < 0.07:
            out[-1][1] = e
        else:
            out.append([s, e, q])
    return out


def contour(m, K, dt=HOP / SR, step=0.05):
    """The sung line every 50 ms on the karaoke timeline, for drawing the guide's fine shape (slides, vibrato).
    Stored as runs: [[start time, step, [midi, ...]], ...] so silences take no space."""
    k = int(round(step / dt))
    runs, cur = [], None
    for i in range(0, len(m) - k + 1, k):
        seg = m[i:i + k]
        v = seg[np.isfinite(seg)]
        if len(v) > k // 2:
            val = round(float(np.median(v)), 2)
            if cur is None:
                t0, t1 = K(i * dt), K((i + 400) * dt)
                cur = [round(t0, 3), round((t1 - t0) / 400 * k, 4), []]
                runs.append(cur)
            cur[2].append(val)
        else:
            cur = None
    return runs


# ---------------------------------------------------------------- the whole thing
def build(kara_path, title, duration, media, work, ytdlp, original=None, procs=4):
    """Returns (notes [[start, dur, midi]], contour, analysis) on the karaoke timeline, or raises."""
    say('original', 0.05, 'Looking for the original song')
    if original:
        cands = [{'id': (re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})', original) or [None, 'given'])[1],
                  'title': 'the link you gave', 'url': original, 'duration': None}]
        q = None
    else:
        own = re.search(r'([\w-]{11})', os.path.basename(os.path.dirname(work)) or '')
        q, cands = find_candidates(ytdlp, title, duration, exclude={own.group(1)} if own else ())
        if not cands:
            raise RuntimeError(f'no original recording was found on YouTube for "{q}"')
    say('original', 0.1, 'Comparing with the karaoke track')
    xk = load_audio(kara_path, CSR)
    best, err = None, ''
    for i, c in enumerate(cands):
        try:
            path = download_audio(ytdlp, c['url'], os.path.join(work, f'orig-{c["id"]}.m4a'))
        except Exception as e:
            err = str(e)
            continue
        try:
            to, tk, shift, corr, gain = align(xk, load_audio(path, CSR))
        except Exception as e:
            err = f'{c["title"]} could not be compared: {e}'
            continue
        c.update(path=path, map=(to, tk), shift=shift, match=corr, gain=gain)
        say('original', 0.1 + 0.2 * (i + 1) / len(cands), f'Compared {i + 1} of {len(cands)} recordings')
        if best is None or gain > best['gain']:
            best = c
        if gain > 0.18:      # clearly the same song; no need to look further
            break
    if best is None:
        raise RuntimeError(err or 'the original song could not be downloaded')
    if best['gain'] < 0.12:
        if original:
            raise RuntimeError('the song at the link you gave does not match the karaoke track')
        raise RuntimeError(f'no recording on YouTube matched the karaoke track well enough (closest: {best["title"]})')
    say('vocals', 0.35, 'Separating the vocal from the original song')
    voc = separate(media, best['path'], os.path.join(work, f'vocals-{best["id"]}.wav'))
    say('pitch', 0.75, 'Following the sung pitch')
    x = load_audio(voc, SR)
    midi, ap, rms = yin_track(x)
    m = clean_pitch(midi, ap, rms)
    tune = tuning_offset(m)
    m = m - tune + best['shift']
    notes = to_notes(m)
    if len(notes) < 10:
        raise RuntimeError('too little singing was found in the original song')
    to, tk = best['map']
    K0 = lambda t: float(np.interp(t, to, tk))
    say('align', 0.78, 'Reading the lyric wipe to fine-tune the timing')
    try:
        onsets = wipe_onsets(wipe_activity(kara_path, duration, procs))
    except Exception:
        onsets = np.array([])
    K, snapped, phrases = snap_phrases(notes, K0, onsets)
    out = []
    for s0, e0, p in notes:
        a, b = K(s0), K(e0)
        if b - a >= 0.05 and a >= 0 and (not duration or a < duration):
            out.append([round(a, 3), round(b - a, 3), int(p)])
    sung = (to >= notes[0][0]) & (to <= notes[-1][1])
    offs = (tk - to)[sung] if sung.any() else tk - to
    analysis = {'method': 'vocals', 'original': {k: best.get(k) for k in ('id', 'title', 'channel', 'url', 'duration')},
                'offset': round(float(np.median(offs)), 3), 'drift': round(float(offs.max() - offs.min()), 2),
                'key_shift': best['shift'], 'match': round(best['match'], 3), 'gain': round(best['gain'], 3),
                'tuning_cents': round(tune * 100), 'query': q, 'phrases': phrases, 'phrases_on_wipe': snapped}
    return out, contour(m, K), analysis


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Test the vocal pitch guide on its own.')
    ap.add_argument('karaoke')
    ap.add_argument('--title', default='')
    ap.add_argument('--original')
    ap.add_argument('--media', default=os.environ.get('ONGAKU_MEDIA') or os.path.expanduser('~/.local/share/ongaku-renshuu-media'))
    ap.add_argument('--work', default='.')
    a = ap.parse_args()
    own = os.path.join(a.media, 'venv', 'bin', 'yt-dlp')
    ytdlp = [own] if os.path.exists(own) else ['yt-dlp']
    dur = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', a.karaoke]).decode())
    notes, cont, info = build(a.karaoke, a.title, dur, a.media, a.work, ytdlp, a.original)
    print(json.dumps({'notes': len(notes), 'analysis': info, 'first': notes[:12]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
