#!/usr/bin/env python3
"""Build a karaoke pitch guide from the original recording's vocals.

The karaoke track itself has no lead vocal, so this finds the original song on YouTube (or takes
the link you give it), lines it up with the karaoke audio, separates the vocal with Demucs, follows
the sung pitch, and turns it into notes on the karaoke video's timeline.

Lining up is done by comparing the two recordings: chroma (the harmony) finds the offset in short
windows to about 0.1 s, then the beats (onset envelopes) sharpen it to about 10 ms. Fast melodies
need that: a guide 0.1 s late shows the previous note for a third of a short one.

Used by karaoke.py, which falls back to reading the video's own pitch bars when this fails.
Run on its own for testing: vocalguide.py KARAOKE_MEDIA_FILE --title TITLE [--original URL]
"""
import json, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000            # pitch tracking rate
HOP = 160             # 10 ms frames
CSR = 11025           # matching rate
CHOP = 1102           # about 0.1 s matching frames
LATE = 0.01           # notes come out about 10 ms early against the singer (measured on acapellas)
BASS_GAP = 10         # semitones under the sung line that count as bass leaking into the vocal


_SAY = __import__('threading').Lock()


def say(stage, progress=None, message=''):
    # one write per line, so progress from parallel steps never runs together
    line = json.dumps({'stage': stage, 'progress': progress, 'message': message}, ensure_ascii=False) + '\n'
    with _SAY:
        sys.stdout.write(line)
        sys.stdout.flush()


def load_audio(path, sr):
    raw = subprocess.check_output(['ffmpeg', '-loglevel', 'error', '-i', path, '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'])
    return np.frombuffer(raw, np.float32).copy()


# ---------------------------------------------------------------- finding the original
JUNK = r'(カラオケ|karaoke|ガイドなし|ガイド無し|ガイドあり|ガイドメロディ|guide|off\s*vocal|オフボーカル|instrumental|inst\.?|' \
       r'歌詞付き?|lyrics?|full|フル|ver\.?|version|hd|4k|music\s*video|mv|pv|official|公式|音程バー|歌ってみた|cover|カバー|on\s*vocal|' \
       r'原曲キー|キー変更|[+-]\d+key|key\s*[+-]?\d+|高音質|字幕|練習用|インスト|bgm|♬|♪)'


def search_query(title, keep_quoted=False):
    """Strip karaoke words and bracketed tags from a karaoke video's title."""
    if keep_quoted:
        # in an original's own title, 「」 and 『』 usually hold the song's name, so keep what is inside
        t = re.sub(r'[【\[［(（<〈《].*?[】\]］)）>〉》]', ' ', title or '')
        t = re.sub(r'[「『」』]', ' ', t)
    else:
        t = re.sub(r'[【\[［(（「『<〈《].*?[】\]］)）」』>〉》]', ' ', title or '')
    t = re.sub(JUNK, ' ', t, flags=re.I)
    t = re.sub(r'[/／|｜:：~〜\-–—_・,、]+', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t or (title or '').strip()


BAD = re.compile(r'カラオケ|karaoke|instrumental|off\s*vocal|オフボーカル|歌ってみた|cover|カバー|piano|ピアノ|guitar|ギター|'
                 r'orchestra|オーケストラ|remix|リミックス|弾いてみた|叩いてみた|吹いてみた|演奏|music\s*box|オルゴール|8.?bit|'
                 r'nightcore|slowed|sped|trumpet|トランペット|sax|サックス|flute|フルート|violin|バイオリン|ヴァイオリン|'
                 r'cello|チェロ|ocarina|オカリナ|harmonica|ハーモニカ|erhu|二胡|ukulele|ウクレレ|tutorial|lesson|レッスン|'
                 r'midi|synthesia|reaction|リアクション|踊ってみた|dance|\bamv\b|\bmad\b|総集編|解説|切り抜き|'
                 r'acapella|a cappella|アカペラ|vocals? only|比較|聴き比べ', re.I)
# a different performance of the song, or an edit with extra sound (dialogue, effects) over it
LIVE = re.compile(r'live|ライブ|concert|コンサート|tour|ツアー|生歌|弾き語り|acoustic|アコースティック|first take|'
                  r'award|アワード|紅白|music station|performance|on stage|ステージ|\bfes\b|フェス|collab|コラボ|[@＠]|体育館|武道館|'
                  r'ドーム|アリーナ|ホール|\bver\b|ver\.|バージョン|audition|オーディション', re.I)
GOOD = re.compile(r'official|公式|- topic|provided to youtube|audio|オフィシャル|music video|\bmv\b|\bpv\b', re.I)


def find_candidates(ytdlp, title, duration, want=4, exclude=()):
    """Search YouTube for the original recording; rank by how close the length is and how official it looks."""
    q = search_query(title)
    seen, cands = set(), []

    def search(query):
        try:
            out = subprocess.run(ytdlp + ['--no-warnings', '--flat-playlist', '-J', f'ytsearch10:{query}'],
                                 capture_output=True, text=True, timeout=90).stdout
            return (json.loads(out) or {}).get('entries') or []
        except Exception:
            return []
    with ThreadPoolExecutor(2) as ex:        # both searches at once
        results = list(ex.map(search, (q, q + ' official audio')))
    for entries in results:
        for e in entries:
            vid, d = e.get('id'), e.get('duration')
            if not vid or vid in seen or not d or vid in exclude:
                continue
            seen.add(vid)
            name = f"{e.get('title') or ''} {e.get('channel') or e.get('uploader') or ''}"
            if BAD.search(name):
                continue
            # a music video often runs longer than the song (a story intro or outro); lining up copes with
            # that, but a shorter upload is usually a cut-down (TV size) version
            over = d - duration
            cost = abs(over) / max(30.0, duration) * (5 if over > 0 else 10)
            if over > max(60, 0.35 * duration) or -over > max(25, 0.15 * duration):
                cost += 6
            if LIVE.search(name):
                cost += 2.5
            if GOOD.search(name):
                cost -= 1
            if e.get('channel_is_verified'):
                # a verified channel named after the artist is the artist's own; fan channels use the name too
                channel = (e.get('channel') or '').lower()
                cost -= 1 if any(len(w) >= 2 and w.lower() in channel for w in q.split()) else 0.5
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


def window_lags(Ck, Co, prior, reach, win_s=10.0, hop_s=1.0):
    """The karaoke-minus-original offset measured in short windows of the original.
    prior(t) gives the expected offset at original time t (seconds); each window searches reach seconds
    either side of it. Returns arrays (window centre, offset, correlation, margin over the next best
    peak at least 0.6 s away), with the offset refined between frames."""
    dt = CHOP / CSR
    Wn, hop, R = int(win_s / dt), int(hop_s / dt), int(reach / dt)
    out = []
    for j in range(0, max(1, len(Co) - Wn), hop):
        seg = Co[j:j + Wn]
        c0 = int(round(prior((j + Wn / 2) * dt) / dt)) + j
        lo, hi = max(0, c0 - R), min(len(Ck) - Wn, c0 + R)
        if hi - lo < 3:
            continue
        idx = np.arange(lo, hi + 1)
        # correlation of this window against every placement in the karaoke track
        V = np.lib.stride_tricks.sliding_window_view(Ck[lo:hi + Wn], Wn, axis=0)     # (n, 12, Wn)
        sc = np.einsum('nkw,wk->n', V, seg) / Wn
        b = int(np.argmax(sc))
        if b in (0, len(sc) - 1) and (lo > c0 - R or hi < c0 + R):
            continue                  # pushed against the start or end of the karaoke track: no real match
        far = np.abs(idx - idx[b]) * dt >= 0.6
        margin = float(sc[b] - (sc[far].max() if far.any() else 0))
        f = 0.0
        if 0 < b < len(sc) - 1:
            den = sc[b - 1] - 2 * sc[b] + sc[b + 1]
            f = 0.5 * (sc[b - 1] - sc[b + 1]) / den if den < 0 else 0.0
        out.append(((j + Wn / 2) * dt, (idx[b] + f - j) * dt, float(sc[b]), margin))
    return np.array(out).reshape(-1, 4)


def running_median(t, v, w, span):
    """Median of the values whose time lies within span/2 of each point, weighted by w."""
    out = np.empty(len(v))
    for i, ti in enumerate(t):
        k = np.abs(t - ti) <= span / 2
        o = np.argsort(v[k])
        cw = np.cumsum(w[k][o])
        out[i] = v[k][o][np.searchsorted(cw, cw[-1] / 2)]
    return out


def offset_map(Ck, Co, lag, dur_o):
    """Original time to karaoke time, from window offsets rather than one long warping path.
    Karaoke tracks are usually played at the original tempo, so the offset changes slowly; a warping path
    on its own wanders where the two versions differ (intros, endings, a repeated phrase that matches the
    wrong repeat). Here every window is matched near the overall offset, unclear windows are dropped,
    stray ones are voted out by their neighbours, and the ends hold the nearest good value."""
    dt = CHOP / CSR
    g = lag * dt
    wl = window_lags(Ck, Co, lambda t: g, 8.0)
    good = (wl[:, 2] > 0.45) & (wl[:, 3] > 0.03)
    if good.sum() < 5:
        return np.array([0.0, dur_o]), np.array([g, g])
    t, o, w = wl[good, 0], wl[good, 1], wl[good, 2] * (wl[good, 3] + 0.02)
    smooth = running_median(t, o, w, 24.0)
    keep = np.abs(o - smooth) < 0.5          # a window that matched another repeat of the phrase
    t, o, w = t[keep], o[keep], w[keep]
    # second pass: each window again, now searching only near the trusted line
    prior = lambda x: float(np.interp(x, t, running_median(t, o, w, 16.0)))
    wl = window_lags(Ck, Co, prior, 0.6, win_s=8.0, hop_s=0.5)
    good = (wl[:, 2] > 0.45) & (wl[:, 3] > 0.0)
    if good.sum() >= 5:
        t2, o2, w2 = wl[good, 0], wl[good, 1], wl[good, 2]
        ok = np.abs(o2 - np.array([prior(x) for x in t2])) < 0.45
        if ok.sum() >= 5:
            t, o, w = t2[ok], o2[ok], w2[ok]
    o = running_median(t, o, w, 6.0)
    return t, o


OHOP = 110            # onset envelope hop, 10 ms at CSR


def onset_env(x, n=1024):
    """Spectral flux every 10 ms: where notes and drum hits start. Sharper in time than chroma."""
    win = np.hanning(n).astype(np.float32)
    count = max(1, (len(x) - n) // OHOP)
    view = np.lib.stride_tricks.sliding_window_view(x, n)
    prev, out = None, np.zeros(count, np.float32)
    for i0 in range(0, count, 2048):
        idx = np.arange(i0, min(count, i0 + 2048))
        S = np.log1p(20 * np.abs(np.fft.rfft(view[idx * OHOP] * win, axis=1)))
        D = np.diff(np.vstack([S[:1] if prev is None else prev, S]), axis=0)
        out[idx] = np.maximum(D, 0).sum(1)
        prev = S[-1:]
    out -= np.convolve(out, np.ones(51) / 51, 'same')
    return np.maximum(out, 0)


def refine_onsets(Ek, Eo, t, o, reach=0.15, win_s=8.0, hop_s=1.0, pool_s=15.0):
    """Sharpen the offset to about 10 ms by matching the onset envelopes near the chroma estimate.
    The search stays inside reach seconds, under a beat. Within that, a pattern that repeats every eighth
    note can still match one step off in a single window, so each window's scores are pooled with its
    neighbours' before the best offset is picked: the true offset scores well all along, a slip only here and there."""
    dt = OHOP / CSR
    Wn, R = int(win_s / dt), int(reach / dt)
    lo_all = int(np.floor((o.min() - reach) / dt)) - 1
    nL = int(np.ceil((o.max() + reach) / dt)) + 2 - lo_all
    cs, rows = [], []
    for c in np.arange(win_s / 2, len(Eo) * dt - win_s / 2, hop_s):
        j = int((c - win_s / 2) / dt)
        seg = Eo[j:j + Wn]
        if seg.std() < 1e-6:
            continue
        seg = (seg - seg.mean()) / seg.std()
        g0 = int(round(float(np.interp(c, t, o)) / dt))
        lags = np.arange(g0 - R, g0 + R + 1)
        lags = lags[(j + lags >= 0) & (j + lags + Wn <= len(Ek))]
        if len(lags) < 3:
            continue
        V = np.lib.stride_tricks.sliding_window_view(Ek[j + lags[0]:j + lags[-1] + Wn], Wn)
        V = (V - V.mean(1, keepdims=True)) / (V.std(1, keepdims=True) + 1e-9)
        row = np.full(nL, np.nan)
        row[lags - lo_all] = V @ seg / Wn
        cs.append(c)
        rows.append(row)
    if len(cs) < 5:
        return t, o, False
    cs, M = np.array(cs), np.array(rows)
    pt, po, pw = [], [], []
    with np.errstate(all='ignore'), __import__('warnings').catch_warnings():
        __import__('warnings').simplefilter('ignore', RuntimeWarning)
        for i, c in enumerate(cs):
            near = np.abs(cs - c) <= pool_s
            pooled = np.nanmean(M[near], 0)
            own = np.isfinite(M[i])
            if own.sum() < 3:
                continue
            k = np.nonzero(own)[0]
            b = k[np.argmax(pooled[k])]
            if pooled[b] < 0.15 or b in (k[0], k[-1]):      # weak, or the best is at the edge of the search
                continue
            pt.append(c)
            po.append((b + lo_all) * dt)
            pw.append(float(pooled[b]))
    if len(pt) < 5:
        return t, o, False
    pt, po, pw = np.array(pt), np.array(po), np.array(pw)
    return pt, running_median(pt, po, pw, 20.0), True


def align(kara, orig):
    """Line the original up with the karaoke track.
    Returns (original times, karaoke times) for mapping, key shift (karaoke = original + shift),
    correlation, gain over chance (about 0.2 for the same song, under 0.1 for others), and whether the
    timing was sharpened to about 10 ms from the beats (False leaves it at about 0.1 s)."""
    Ck, Co0 = chroma(kara), chroma(orig)
    lag, shift, corr, gain = global_match(Ck, Co0)
    Co = np.roll(Co0, shift, axis=1)
    dur_o = len(orig) / CSR
    t, o = offset_map(Ck, Co, lag, dur_o)
    t, o, fine = refine_onsets(onset_env(kara), onset_env(orig), t, o)
    dt = CHOP / CSR
    to = np.arange(0, dur_o + dt, dt)
    tk = np.maximum.accumulate(to + np.interp(to, t, o))     # constant beyond the first and last good window
    return to, tk, shift, corr, gain, fine


# ---------------------------------------------------------------- the karaoke video's lyric wipe
WFPS = 12.5           # wipe analysis frame rate
WW, WH = 480, 270     # wipe analysis size
WBANDS = 18           # horizontal bands, so busy parts of the picture can be left out


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
                out[c] = m.reshape(WBANDS, -1).sum(1).astype(np.int32)
        k += 1
    p.wait()
    return out


def wipe_activity(path, duration, procs):
    from multiprocessing import Pool
    total = int(duration * WFPS) + 1
    n = max(1, procs * 2)
    edges = np.linspace(0, total, n + 1).astype(int)
    bands = np.zeros((total, WBANDS), np.float32)
    with Pool(procs) as pool:
        for k, part in enumerate(pool.imap_unordered(_wipe_chunk, [(path, int(a), int(b)) for a, b in zip(edges, edges[1:])])):
            for i, val in part.items():
                bands[i] = val
            say('align', 0.78 + 0.1 * (k + 1) / n, 'Reading the lyric wipe to fine-tune the timing')
    # Lyrics are quiet most of the time and change in bursts as a line is wiped. Bands that change all
    # the time (moving backgrounds, spectrum displays, scrolling pitch bars) are left out.
    busy = (bands > 8).mean(0)
    keep = (busy < 0.45) & (bands.sum(0) > 0)
    return bands[:, keep].sum(1) if keep.any() else bands.sum(1)


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
    msg = 'Separating the vocal from the original song'
    for line in p.stdout:
        tail = (tail + [line.strip()])[-5:]
        m = re.match(r'progress ([\d.]+)', line)
        if m:
            say('vocals', 0.35 + 0.35 * float(m.group(1)), msg)
        elif line.startswith('cpu '):
            msg = 'Separating the vocal on the CPU (the graphics card is busy), this takes longer'
            say('vocals', None, msg)
    p.wait()
    if p.returncode != 0 or not os.path.exists(out_wav):
        raise RuntimeError('the vocal could not be separated: ' + ' '.join(tail[-2:]))
    return out_wav


# ---------------------------------------------------------------- following the sung pitch
def yin_track(x, sr=SR, hop=HOP, win=640, fmin=60.0, fmax=1000.0, thr=0.18, floor_midi=None):
    """YIN pitch per 10 ms frame. Returns midi (nan when unvoiced), aperiodicity, and rms.
    floor_midi (per frame, nan for none) keeps each frame's pitch above that note, so a louder bass
    line leaking under the voice is not picked instead of it."""
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
        if floor_midi is not None:
            fl = floor_midi[idx]
            lim = np.where(np.isfinite(fl), sr / (440 * 2 ** ((np.nan_to_num(fl) - 69) / 12)), tmax + 1)
            cm = np.where(taus[None, :] > lim[:, None], 1.0, cm)
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


def frame_rms(x, hop=HOP, win=640):
    n = max(1, (len(x) - win) // hop)
    view = np.lib.stride_tricks.sliding_window_view(x, win)[np.arange(n) * hop]
    return np.sqrt((view.astype(np.float64) ** 2).mean(1) + 1e-12)


def vocal_pitch(midi, ap, xv, xo, ratio_db=-26.0, gap=0.25, ap_max=0.45, floor=0.03, short=0.15, smooth=5, below=None):
    """Decide where the singer is actually singing and clean up the pitch line.

    Singing means YIN found a periodic pitch AND the separated vocal is a real part of the original
    mix there (not just instrument bleed left in the stem, which is far quieter than the mix).
    Then: octave slips are folded back, tiny islands dropped, short dropouts inside a phrase bridged,
    and the line is lightly smoothed. Returns (pitch line in semitones, vocal-to-mix ratio in dB)."""
    n = len(midi)
    rv, ro = frame_rms(xv)[:n], frame_rms(xo)[:n]
    if len(rv) < n:
        rv, ro = np.pad(rv, (0, n - len(rv))), np.pad(ro, (0, n - len(ro)))
    ratio = 20 * np.log10(np.maximum(rv, 1e-9) / np.maximum(ro, 1e-9))
    loud = np.percentile(rv, 95) if len(rv) else 1
    voiced = np.isfinite(midi) & (ap < ap_max) & (ratio > ratio_db) & (rv > loud * floor)
    m = np.where(voiced, midi, np.nan)
    ref = medfilt(m, 61)
    for k in (12, -12, 24, -24):
        slip = np.isfinite(ref) & (np.abs(m - ref - k) < 3)
        m[slip] -= k
    if below:
        # far under the line around it, in the middle of singing: the bass leaking into the vocal stem
        wide = medfilt(m, 201)
        m[np.isfinite(wide) & (m < wide - below)] = np.nan
    v = np.isfinite(m)
    d = np.diff(np.r_[0, v.astype(np.int8), 0])
    for s0, e0 in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
        if e0 - s0 < 5:
            m[s0:e0] = np.nan
    g, gs, i = int(gap * SR / HOP), int(short * SR / HOP), 0
    while i < n:
        if np.isnan(m[i]):
            j = i
            while j < n and np.isnan(m[j]):
                j += 1
            if 0 < i and j < n and j - i <= g and abs(m[i - 1] - m[j]) < 2.5:
                m[i:j] = np.linspace(m[i - 1], m[j], j - i + 2)[1:-1]
            elif 0 < i and j < n and j - i <= gs:
                # a consonant between two sung notes: each side holds its pitch up to the middle
                h = (i + j) // 2
                m[i:h], m[h:j] = m[i - 1], m[j]
            i = j
        else:
            i += 1
    ms = medfilt(m, smooth) if smooth > 1 else m.copy()
    ms[np.isnan(m)] = np.nan
    return ms, ratio


def sung_line(x, xo):
    """The cleaned sung pitch line of the vocal stem x (with xo, the original mix, for the bleed check).
    Where the pitch falls far under the sung line around it, the bass has leaked into the stem: those
    frames are tracked again, kept above that line. Returns (line in semitones, vocal-to-mix ratio)."""
    midi, ap, _ = yin_track(x, thr=0.25)
    m, ratio = vocal_pitch(midi, ap, x, xo)
    wide = medfilt(m, 201)
    low = np.isfinite(midi) & np.isfinite(wide) & (midi < wide - BASS_GAP)
    if low.any():
        midi2, ap2, _ = yin_track(x, thr=0.25, floor_midi=np.where(low, wide - BASS_GAP, np.nan))
        # the bass still muddies these frames, so a pitch that lands back on the line is trusted
        fix = low & (np.abs(midi2 - wide) < 7)
        midi, ap = np.where(fix, midi2, midi), np.where(fix, np.minimum(ap2, 0.3), ap)
        m, ratio = vocal_pitch(midi, ap, x, xo)
    return m, ratio


def tuning_offset(m):
    """How far the recording sits from A440, in semitones (-0.5..0.5)."""
    v = m[np.isfinite(m)]
    if len(v) < 50:
        return 0.0
    ang = 2 * np.pi * (v - np.round(v))
    return float(np.angle(np.exp(1j * ang).mean()) / (2 * np.pi))


def dp_notes(m, dt, cost):
    """Split each sung phrase into the semitone notes that keep the most of the sung line within half a
    semitone of its note, paying `cost` (in frames) for every new note: the best such split, found
    with dynamic programming. A higher cost gives fewer, longer notes."""
    notes = []
    v = np.isfinite(m)
    d = np.diff(np.r_[0, v.astype(np.int8), 0])
    for s0, e0 in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]):
        x = m[s0:e0]
        P = np.arange(int(np.floor(x.min())) - 1, int(np.ceil(x.max())) + 2)
        # 0 within half a semitone, rising to 1 a full semitone away
        c = np.clip((np.abs(x[:, None] - P[None, :]) - 0.5) / 0.5, 0, 1)
        T, K = c.shape
        D = c[0].copy()
        back = np.zeros((T, K), np.int32)
        for t in range(1, T):
            j = int(np.argmin(D))
            switch = D[j] + cost
            stay = D <= switch
            back[t] = np.where(stay, np.arange(K), j)
            D = np.where(stay, D, switch) + c[t]
        k = int(np.argmin(D))
        path = np.empty(T, np.int32)
        for t in range(T - 1, -1, -1):
            path[t] = k
            k = back[t, k]
        ch = np.nonzero(np.diff(path))[0] + 1
        for a, b in zip(np.r_[0, ch], np.r_[ch, T]):
            notes.append([(s0 + a) * dt, (s0 + b) * dt, int(P[path[a]])])
    return notes


def to_notes(m, ratio=None, dt=HOP / SR, minnote=0.03, note_db=-24.0, merge_gap=0.03, repitch=True, refine=0.1, split=0.7, hold=4,
             min_sung=0.03, iso=0.12, dp=0.0):
    """Split the pitch line into karaoke-style notes.
    A new note starts only when the pitch stays more than 0.7 semitone from the current one for 60 ms,
    so vibrato and scoops do not chop a held note. Fragments shorter than minnote join the neighbour
    nearest in pitch, notes where the vocal is too quiet against the mix are dropped (bleed), and a note
    an octave away from the notes around it is moved back."""
    n = len(m)
    notes, i = [], 0
    if dp:
        notes = dp_notes(m, dt, dp)
        i = n
    while i < n:
        if not np.isfinite(m[i]):
            i += 1
            continue
        head = m[i:i + 5]
        cur = int(round(float(np.median(head[np.isfinite(head)]))))
        start, j, run0 = i, i, None
        while j < n and np.isfinite(m[j]):
            if abs(m[j] - cur) > split:
                run0 = j if run0 is None else run0
                if j - run0 + 1 >= hold:
                    notes.append([start * dt, run0 * dt, cur])
                    start, cur, run0 = run0, int(round(float(np.median(m[run0:j + 1])))), None
            else:
                run0 = None
            j += 1
        notes.append([start * dt, j * dt, cur])
        i = j
    changed = True
    while changed:
        changed = False
        for k, (a, b, p) in enumerate(notes):
            if b - a >= minnote:
                continue
            L = notes[k - 1] if k > 0 and a - notes[k - 1][1] < 0.03 else None
            R = notes[k + 1] if k + 1 < len(notes) and notes[k + 1][0] - b < 0.03 else None
            if L or R:
                tgt = min([x for x in (L, R) if x], key=lambda x: abs(x[2] - p))
                if tgt is L:
                    L[1] = b
                else:
                    R[0] = a
            notes.pop(k)
            changed = True
            break
    if ratio is not None:
        # judge each note by its sung part: a clearly sung note whose line runs on into a quiet tail
        # (the voice fading under the band) is trimmed, not thrown away with the tail
        kept = []
        for a, b, p in notes:
            i0 = int(a / dt)
            r = ratio[i0:max(i0 + 1, int(b / dt))]
            ok = np.nonzero(r >= note_db)[0]
            if not len(ok):
                continue
            s0, s1 = ok[0], ok[-1] + 1
            if (s1 - s0) * dt >= min_sung and np.median(r[s0:s1]) >= note_db:
                kept.append([(i0 + s0) * dt, (i0 + s1) * dt, p])
        notes = kept
    if repitch:
        # a note's pitch is where the voice settles, not the scoop it starts with: the median of its
        # sung frames with the first and last 30 ms left out
        for nt in notes:
            i0, i1 = int(round(nt[0] / dt)), int(round(nt[1] / dt))
            if i1 - i0 > 8:
                i0, i1 = i0 + 3, i1 - 3
            v = m[i0:i1]
            v = v[np.isfinite(v)]
            if len(v):
                nt[2] = int(round(float(np.median(v))))
    if refine:
        # move the line between two touching notes to where the sung pitch actually crosses from one to
        # the other (the frames closest to each note's pitch), at most `refine` seconds either way
        r = int(round(refine / dt))
        for L, R in zip(notes, notes[1:]):
            if L[2] == R[2] or R[0] - L[1] > 0.03:
                continue
            b0 = int(round(L[1] / dt))
            keep = int(round(minnote / dt))      # never squeeze a note below the shortest allowed
            lo, hi = max(int(round(L[0] / dt)) + keep, b0 - r), min(int(round(R[1] / dt)) - keep, b0 + r)
            if hi <= lo:
                continue
            seg = m[lo:hi]
            # score of each split point: frames left of it near L's pitch plus frames right of it near R's
            okL = np.nan_to_num(np.abs(seg - L[2]) < np.abs(seg - R[2])).astype(np.int32)
            gain = np.r_[0, np.cumsum(okL)] + np.r_[np.cumsum((1 - okL)[::-1])[::-1], 0]
            cut = lo + int(np.argmax(gain))
            L[1] = R[0] = cut * dt
    for _ in range(2):
        for k, (a, b, p) in enumerate(notes):
            ctx = [(x[1] - x[0], x[2]) for j2, x in enumerate(notes) if j2 != k and abs(x[0] - a) < 3]
            if len(ctx) < 2:
                continue
            w = np.array([c[0] for c in ctx])
            vals = np.array([c[1] for c in ctx])
            o = np.argsort(vals)
            med = float(vals[o][np.searchsorted(np.cumsum(w[o]), w.sum() / 2)])
            if abs(p - med) >= 9:
                for kk in (12, -12):
                    if abs(p + kk - med) <= 6:
                        notes[k][2] = p + kk
                        break
    if iso:
        # a lone blip with nothing sung around it is a breath, a click or bleed, not a note to sing
        notes = [x for k, x in enumerate(notes) if x[1] - x[0] >= iso
                 or (k > 0 and x[0] - notes[k - 1][1] < 0.15) or (k + 1 < len(notes) and notes[k + 1][0] - x[1] < 0.15)]
    out = []
    for a, b, p in notes:
        if out and out[-1][2] == p and a - out[-1][1] < merge_gap:
            out[-1][1] = b
        else:
            out.append([a, b, p])
    return out


def contour(m, notes, K, dt=HOP / SR, step=0.05):
    """The sung line every 50 ms on the karaoke timeline, for drawing the guide's fine shape (slides,
    vibrato). Only drawn inside notes, folded into each note's octave so it never jumps away from the bars.
    Stored as runs: [[start time, step, [midi, ...]], ...] so silences take no space."""
    k = int(round(step / dt))
    runs = []
    for a, b, p in notes:
        i0, i1 = int(a / dt), int(b / dt)
        vals = []
        for i in range(i0, max(i0 + 1, i1 - k + 1), k):
            seg = m[i:i + k]
            v = seg[np.isfinite(seg)]
            if len(v) <= k // 2:
                vals.append(round(float(p), 2))
                continue
            x = float(np.median(v))
            while x - p > 6:
                x -= 12
            while p - x > 6:
                x += 12
            # a glitch far from the note is not part of the sung line: hold the last good value
            vals.append(round(x, 2) if abs(x - p) <= 2.5 else (vals[-1] if vals else round(float(p), 2)))
        if len(vals) >= 3:
            sm = [vals[0]] + [sorted(vals[q - 1:q + 2])[1] for q in range(1, len(vals) - 1)] + [vals[-1]]
            vals = [round(float(x), 2) for x in sm]
        t0, t1 = K(a), K(b)
        if vals and t1 > t0:
            runs.append([round(t0, 3), round((t1 - t0) / len(vals), 4), vals])
    return runs


# ---------------------------------------------------------------- the whole thing
def build(kara_path, title, duration, media, work, ytdlp, original=None, procs=4, video=None):
    """Returns (notes [[start, dur, midi]], contour, analysis) on the karaoke timeline, or raises.
    kara_path can be the karaoke audio alone, or a function returning its path once it has downloaded (so
    the search can start first); video (the same) is only needed for the lyric-wipe fallback."""
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
    # the first three are compared anyway, so fetch them together
    ex = ThreadPoolExecutor(3)
    fetch = {c['id']: ex.submit(download_audio, ytdlp, c['url'], os.path.join(work, f'orig-{c["id"]}.m4a')) for c in cands[:3]}
    if callable(kara_path):
        kara_path = kara_path()
    xk = load_audio(kara_path, CSR)
    best, err = None, ''
    for i, c in enumerate(cands):
        try:
            path = fetch[c['id']].result() if c['id'] in fetch else \
                download_audio(ytdlp, c['url'], os.path.join(work, f'orig-{c["id"]}.m4a'))
        except Exception as e:
            err = str(e)
            continue
        try:
            to, tk, shift, corr, gain, fine = align(xk, load_audio(path, CSR))
        except Exception as e:
            err = f'{c["title"]} could not be compared: {e}'
            continue
        c.update(path=path, map=(to, tk), shift=shift, match=corr, gain=gain, fine=fine)
        say('original', 0.1 + 0.2 * (i + 1) / len(cands), f'Compared {i + 1} of {len(cands)} recordings')
        if best is None or gain > best['gain']:
            best = c
        if i >= 2 and best is not None and best['gain'] > 0.18:
            break        # the best of the first three; later ones rank lower and are rarely better
    ex.shutdown(wait=False, cancel_futures=True)
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
    m, ratio = sung_line(x, load_audio(best['path'], SR))
    tune = tuning_offset(m)
    m = m - tune + best['shift']
    notes = to_notes(m, ratio)
    if len(notes) < 10:
        raise RuntimeError('too little singing was found in the original song')
    to, tk = best['map']
    K0 = lambda t: float(np.interp(t, to, tk))
    if best['fine']:
        # the beats already line the two recordings up to about 10 ms; the lyric wipe usually runs a little
        # ahead of the voice, so snapping to it would only make the timing worse
        K, snapped, phrases = K0, 0, 0
    else:
        say('align', 0.78, 'Reading the lyric wipe to fine-tune the timing')
        try:
            onsets = wipe_onsets(wipe_activity((video() if callable(video) else video) or kara_path, duration, procs))
        except Exception:
            onsets = np.array([])
        K, snapped, phrases = snap_phrases(notes, K0, onsets)
    Kd = K
    K = lambda t: Kd(t) + LATE
    out = []
    for s0, e0, p in notes:
        a, b = K(s0), K(e0)
        if b - a >= 0.03 and a >= 0 and (not duration or a < duration):
            out.append([round(a, 3), round(b - a, 3), int(p)])
    sung = (to >= notes[0][0]) & (to <= notes[-1][1])
    offs = (tk - to)[sung] if sung.any() else tk - to
    analysis = {'method': 'vocals', 'original': {k: best.get(k) for k in ('id', 'title', 'channel', 'url', 'duration')},
                'offset': round(float(np.median(offs)), 3), 'drift': round(float(offs.max() - offs.min()), 2),
                'key_shift': best['shift'], 'match': round(best['match'], 3), 'gain': round(best['gain'], 3),
                'tuning_cents': round(tune * 100), 'query': q, 'timing': 'beats' if best['fine'] else 'chroma',
                'phrases': phrases, 'phrases_on_wipe': snapped}
    return out, contour(m, notes, K), analysis


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
