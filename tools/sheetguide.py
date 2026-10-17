#!/usr/bin/env python3
"""Sheet-music pitch guides for karaoke songs, for Ongaku Renshuu.

A written melody (an UltraStar note file from the UltraStar song database, USDB, or a file of your own:
UltraStar .txt, MIDI or MusicXML) is lined up with the singer's pitch line that vocalguide.py found in the
original recording (work/line.npz), then moved onto the karaoke video's timeline. It is only kept when it
follows the singer closely; otherwise the song keeps its other guides.

Usage: sheetguide.py SONG_DIR --file PATH          line up a file and add it to the song as its sheet guide
       sheetguide.py SONG_DIR --usdb               search USDB (needs a login) and add the best match
       sheetguide.py --usdb-login USER PASS         check a USDB login and keep it in MEDIA/usdb.json
Progress is printed as JSON lines, like the video importers.
"""
import argparse, base64, gzip, http.cookiejar, io, json, os, re, struct, sys, urllib.parse, urllib.request, zipfile
import xml.etree.ElementTree as ET

import numpy as np

FPS = 20                 # frames a second for the rough match
MATCH_MIN = 0.62         # share of the shared sung frames that must be within a semitone (octave-free)
COVER_MIN = 0.35        # share of the singer's frames the written notes must cover (the line has backing vocals too)
USDB = 'https://usdb.animux.de/'


def say(stage, progress=None, message='', **k):
    print(json.dumps(dict(stage=stage, progress=progress, message=message, **k), ensure_ascii=False), flush=True)


def default_media():
    return os.environ.get('ONGAKU_MEDIA') or os.path.join(
        os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'ongaku-renshuu-media')


# ---------------------------------------------------------------- reading written melodies
def parse_ultrastar(text):
    """UltraStar .txt: header tags, then ': start length pitch syllable' lines in beats (pitch 0 is C4).
    Returns {title, artist, notes: [(t0, t1, midi, syllable)], timed: True} in the file's own seconds.
    For duets only the first singer is kept."""
    head, notes, rel, player = {}, [], 0, None
    for raw in text.splitlines():
        line = raw.rstrip('\r\n')
        if line.startswith('#'):
            k, _, v = line[1:].partition(':')
            head[k.strip().upper()] = v.strip()
            continue
        if not line:
            continue
        c = line[0]
        if c == 'P':
            player = line[1:].strip()
            if notes and player not in ('1', '01'):
                break
            continue
        if c == 'E':
            break
        if c in ':*FRG':
            m = re.match(r'[:*FRG]\s*(-?\d+)\s+(\d+)\s+(-?\d+)\s?(.*)$', line)
            if m and c != 'F':
                notes.append((int(m.group(1)) + rel, int(m.group(2)), int(m.group(3)), m.group(4)))
        elif c == '-' and head.get('RELATIVE', '').lower() in ('yes', 'true'):
            p = line[1:].split()
            if p:
                rel += int(p[-1])
    bpm = float(head.get('BPM', '0').replace(',', '.') or 0)
    if not notes or bpm <= 0:
        raise ValueError('that is not an UltraStar note file')
    gap = float(head.get('GAP', '0').replace(',', '.') or 0) / 1000
    beat = 60 / (bpm * 4)
    return {'title': head.get('TITLE', ''), 'artist': head.get('ARTIST', ''), 'timed': True, 'kind': 'ultrastar',
            'notes': [(gap + b * beat, gap + (b + max(1, l)) * beat, 60 + p, s) for b, l, p, s in notes]}


def _vlq(d, i):
    v = 0
    while True:
        b = d[i]
        i += 1
        v = (v << 7) | (b & 0x7f)
        if b < 0x80:
            return v, i


def parse_midi(data):
    """Standard MIDI file: every track's notes in seconds (tempo map applied). Returns the track that is the
    most likely sung melody first: mostly one note at a time, in the singing range, with the most notes."""
    if data[:4] != b'MThd':
        raise ValueError('that is not a MIDI file')
    _, ntr, div = struct.unpack('>HHH', data[8:14])
    i, tracks, tempos = 8 + struct.unpack('>I', data[4:8])[0], [], [(0, 500000)]
    for _ in range(ntr):
        if data[i:i + 4] != b'MTrk':
            break
        n = struct.unpack('>I', data[i + 4:i + 8])[0]
        d, j, tick, run, on, ev = data[i + 8:i + 8 + n], 0, 0, 0, {}, []
        name = ''
        while j < len(d):
            dt, j = _vlq(d, j)
            tick += dt
            st = d[j]
            if st == 0xff:
                typ, ln = d[j + 1], None
                ln, j2 = _vlq(d, j + 2)
                if typ == 0x51:
                    tempos.append((tick, int.from_bytes(d[j2:j2 + 3], 'big')))
                elif typ == 0x03:
                    name = d[j2:j2 + ln].decode('latin-1', 'replace')
                j = j2 + ln
                continue
            if st in (0xf0, 0xf7):
                ln, j = _vlq(d, j + 1)
                j += ln
                continue
            if st & 0x80:
                run = st
                j += 1
            hi, ch = run & 0xf0, run & 0x0f
            if hi in (0xc0, 0xd0):
                j += 1
                continue
            a, b = d[j], d[j + 1]
            j += 2
            if hi == 0x90 and b > 0:
                on[(ch, a)] = tick
            elif hi in (0x80, 0x90) and (ch, a) in on:
                ev.append((on.pop((ch, a)), tick, a, ch))
        tracks.append((name, ev))
        i += 8 + n
    tempos.sort()

    def sec(tk):
        if div & 0x8000:
            return tk / ((256 - (div >> 8)) * (div & 0xff))
        s, last, us = 0.0, 0, 500000
        for t0, u in tempos:
            if t0 >= tk:
                break
            s += (t0 - last) * us / div / 1e6
            last, us = t0, u
        return s + (tk - last) * us / div / 1e6
    out = []
    for name, ev in tracks:
        ev = [e for e in ev if e[3] != 9]           # never the drums
        if len(ev) < 20:
            continue
        notes = [(sec(a), sec(b), p, '') for a, b, p, _ in sorted(ev)]
        out.append({'name': name, 'notes': monophonic(notes)})

    def rank(t):
        ps = np.array([n[2] for n in t['notes']])
        sing = float(((ps >= 48) & (ps <= 84)).mean())
        vocal = 1.5 if re.search(r'vo(cal|ice|x)|melod|sing|lead|歌|メロ|ボーカル', t['name'], re.I) else 1.0
        return vocal * sing * len(ps)
    out.sort(key=rank, reverse=True)
    if not out:
        raise ValueError('that MIDI file has no melody track')
    return [{'title': '', 'artist': '', 'timed': True, 'kind': 'midi', 'notes': t['notes'], 'track': t['name']} for t in out[:4]]


def monophonic(notes):
    """One note at a time: of notes sounding together, the highest; a note is cut where the next one starts."""
    by = {}
    for n in notes:
        k = round(n[0], 3)
        if k not in by or n[2] > by[k][2]:
            by[k] = n
    ns = sorted(by.values())
    return [(a, min(b, ns[k + 1][0]) if k + 1 < len(ns) else b, p, s) for k, (a, b, p, s) in enumerate(ns)
            if (min(b, ns[k + 1][0]) if k + 1 < len(ns) else b) > a]


STEP = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def parse_musicxml(data):
    """MusicXML (.musicxml, .xml or compressed .mxl): each part's notes in seconds at the written tempo
    (120 a minute when none is written; the line-up finds the real tempo). The part most like a sung melody
    comes first."""
    if data[:2] == b'PK':
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if n.lower().endswith(('.xml', '.musicxml')) and not n.startswith('META-INF')]
        data = z.read(names[0])
    root = ET.fromstring(data)
    for el in root.iter():
        if '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    out = []
    for part in root.findall('part'):
        div, pos, tempo, sec, notes, last = 1, 0.0, 120.0, 0.0, [], None
        lyric = ''
        for meas in part.findall('measure'):
            for el in meas:
                if el.tag == 'attributes' and el.find('divisions') is not None:
                    div = float(el.find('divisions').text)
                elif el.tag == 'direction' or el.tag == 'sound':
                    snd = el if el.tag == 'sound' else el.find('.//sound')
                    if snd is not None and snd.get('tempo'):
                        tempo = float(snd.get('tempo'))
                elif el.tag in ('backup', 'forward'):
                    d = float(el.findtext('duration') or 0) / div * 60 / tempo
                    sec += -d if el.tag == 'backup' else d
                elif el.tag == 'note':
                    d = float(el.findtext('duration') or 0) / div * 60 / tempo
                    chord = el.find('chord') is not None
                    t0 = sec - (last[1] if chord and last else 0)
                    if el.find('pitch') is not None and el.find('grace') is None:
                        pt = el.find('pitch')
                        midi = 12 * (int(pt.findtext('octave')) + 1) + STEP[pt.findtext('step')] + int(float(pt.findtext('alter') or 0))
                        lyric = el.findtext('lyric/text') or ''
                        tie = [t.get('type') for t in el.findall('tie')]
                        if 'stop' in tie and notes and notes[-1][2] == midi and abs(notes[-1][1] - t0) < 1e-6:
                            notes[-1] = (notes[-1][0], t0 + d, midi, notes[-1][3])
                        else:
                            notes.append((t0, t0 + d, midi, lyric))
                    if not chord:
                        sec += d
                        last = (t0, d)
        if len(notes) >= 20:
            out.append({'name': part.get('id', ''), 'notes': monophonic(notes)})
    if not out:
        raise ValueError('that MusicXML file has no melody in it')
    out.sort(key=lambda t: float(np.mean([(48 <= n[2] <= 84) for n in t['notes']])) * len(t['notes']), reverse=True)
    return [{'title': root.findtext('.//work-title') or root.findtext('.//movement-title') or '', 'artist': '',
             'timed': False, 'kind': 'musicxml', 'notes': t['notes'], 'track': t['name']} for t in out[:4]]


def read_file(path):
    """A written melody from a file: a list of candidate melodies, the likeliest first."""
    data = open(path, 'rb').read()
    if data[:4] == b'MThd':
        return parse_midi(data)
    if data[:2] == b'PK' or data.lstrip()[:5] in (b'<?xml', b'<scor') or b'<score-partwise' in data[:2000]:
        return parse_musicxml(data)
    for enc in ('utf-8-sig', 'cp1252', 'shift_jis'):
        try:
            return [parse_ultrastar(data.decode(enc))]
        except UnicodeDecodeError:
            continue
    raise ValueError('that file is not an UltraStar, MIDI or MusicXML file')


# ---------------------------------------------------------------- lining a melody up with the singer
def _frames(notes, n, fps, scale=1.0, shift=0.0):
    """Pitch class one-hot per frame (12 x n) of written notes, after time = t * scale + shift."""
    a = np.zeros((12, n), np.float32)
    for t0, t1, p, _ in notes:
        i0, i1 = int((t0 * scale + shift) * fps), int((t1 * scale + shift) * fps)
        if i1 > 0 and i0 < n:
            a[p % 12, max(0, i0):min(n, max(i0 + 1, i1))] = 1
    return a


def _corr(A, B, keys=range(12)):
    """For each key k, the cross-correlation of A (written) against B (sung) moved by k semitones: lag L means
    written frame i lines up with sung frame i + L. Returns (keys x lags) and the lag of index 0."""
    n = A.shape[1] + B.shape[1]
    N = 1 << int(np.ceil(np.log2(n)))
    FA, FB = np.fft.rfft(A, N), np.fft.rfft(B, N)
    out = []
    for k in keys:
        out.append(np.fft.irfft((np.conj(FA) * np.roll(FB, -k, 0)).sum(0), N))
    r = np.array(out)
    # lags -len(A)+1 .. len(B)-1
    r = np.concatenate([r[:, N - A.shape[1] + 1:], r[:, :B.shape[1]]], 1)
    return r, -(A.shape[1] - 1)


def _match(notes, m, dt, mapf):
    """How well written notes (mapped by mapf into the sung line's time) follow the sung line m:
    (share of shared frames within a semitone in any octave, share of sung frames covered, octave offset)."""
    n = len(m)
    w = np.full(n, np.nan)
    for t0, t1, p, _ in notes:
        a, b = int(mapf(t0) / dt), int(mapf(t1) / dt)
        if b > 0 and a < n:
            w[max(0, a):min(n, b)] = p
    v = np.isfinite(m)
    both = v & np.isfinite(w)
    if both.sum() < 50:
        return 0.0, 0.0, 0
    d = m[both] - w[both]
    ok = np.abs((d + 6) % 12 - 6) < 1
    octv = int(round(float(np.median(d[ok])) / 12)) * 12 if ok.any() else 0
    return float(ok.mean()), float(both.sum() / max(1, v.sum())), octv


def align(src, m, dt, go, gk, duration=None, timed=True):
    """Line a written melody (src: {'notes': [(t0, t1, midi, syllable)], ...} in its own seconds) up with the
    singer's line m (the original recording's time, frames of dt). go/gk map that time onto the karaoke video.
    Returns (notes [[start, length, midi]] on the karaoke timeline, info) or None when it does not follow
    the singer closely enough."""
    notes = src['notes']
    n = int(len(m) * dt * FPS)
    mm = m[(np.arange(n) / FPS / dt).astype(int).clip(0, len(m) - 1)]
    B = np.zeros((12, n), np.float32)
    v = np.isfinite(mm)
    B[np.round(mm[v]).astype(int) % 12, np.nonzero(v)[0]] = 1
    span = notes[-1][1] - notes[0][0]
    # written tempo: a note file is timed to a recording already; sheet music only has its marked tempo
    scales = np.linspace(0.97, 1.03, 13) if timed else np.geomspace(0.4, 2.5, 160)
    best = None
    for s in scales:
        if span * s > len(m) * dt * 1.6:
            continue
        A = _frames(notes, int((notes[-1][1] * s) * FPS) + 2, FPS, s)
        r, l0 = _corr(A, B)
        k, i = np.unravel_index(int(np.argmax(r)), r.shape)
        sc = float(r[k, i]) / max(1.0, float(A.sum()))
        if best is None or sc > best[0]:
            best = (sc, s, (i + l0) / FPS, k)
    if best is None:
        return None, {'kind': src.get('kind'), 'match': 0.0, 'cover': 0.0, 'error': 'much longer than the song'}
    sc, s, off, key = best
    # sharpen piece by piece: 12 s pieces of the written melody, each moved up to 1.5 s to fit the singer
    ts = np.array([nt[0] for nt in notes])
    fine = int(round(1 / dt))
    pts = []
    for c0 in np.arange(notes[0][0], notes[-1][1], 4.0):
        sel = [nt for nt in notes if c0 <= nt[0] < c0 + 12]
        if len(sel) < 6:
            continue
        A = _frames([(a - c0, b - c0, p + key, x) for a, b, p, x in sel], int(12 * s * fine) + 2, fine, s)
        if A.sum() < 50:
            continue
        base = c0 * s + off
        i0, i1 = int((base - 1.5) / dt), int((base + 1.5 + 12 * s) / dt) + 2
        seg = m[max(0, i0):max(0, i1)]
        if len(seg) < A.shape[1]:
            continue
        Bs = np.zeros((12, len(seg)), np.float32)
        vv = np.isfinite(seg)
        Bs[np.round(seg[vv]).astype(int) % 12, np.nonzero(vv)[0]] = 1
        r, l0 = _corr(A, Bs, keys=[0])
        r = r[0]
        lags = np.arange(len(r)) + l0
        okl = (lags >= 0) & (lags <= len(seg) - A.shape[1])
        if not okl.any():
            continue
        r = np.where(okl, r, -1)
        j = int(np.argmax(r))
        if r[j] < 0.4 * A.sum():
            continue                                   # this piece does not show in the singing (a solo, an edit)
        pts.append((c0 + 6, (max(0, i0) + lags[j]) * dt - c0 * s))   # written time -> where its piece starts
    if len(pts) >= 3:
        pc = np.array([p[0] for p in pts])
        po = np.array([p[1] for p in pts])
        med = np.array([np.median(po[max(0, k - 3):k + 4]) for k in range(len(po))])
        keep = np.abs(po - med) < 0.25
        pc, po = pc[keep], po[keep]
    if len(pts) >= 3 and len(pc) >= 3:
        mapf = lambda t: t * s + float(np.interp(t, pc, po))
    else:
        mapf = lambda t: t * s + off
    match, cover, octv = _match([(a, b, p + key, x) for a, b, p, x in notes], m, dt, mapf)
    info = {'kind': src.get('kind'), 'title': src.get('title', ''), 'artist': src.get('artist', ''),
            'match': round(match, 3), 'cover': round(cover, 3), 'key': int(key if key <= 6 else key - 12),
            'tempo': round(float(s), 3), 'pieces': len(pts)}
    if match < MATCH_MIN or cover < COVER_MIN:
        return None, info
    out = []
    for a, b, p, x in notes:
        ka, kb = float(np.interp(mapf(a), go, gk)), float(np.interp(mapf(b), go, gk))
        if kb - ka >= 0.03 and ka >= 0 and (not duration or ka < duration):
            out.append([round(ka, 3), round(kb - ka, 3), int(p + key + octv)] + ([x] if x else []))
    return out, info


def best_of(cands, line, duration=None):
    """Line up each candidate melody; the one that follows the singer best, as (notes, info), or (None, info)."""
    z = line
    best = (None, None)
    for c in cands:
        try:
            r = align(c, z['m'], float(z['dt']), z['go'], z['gk'], duration, c.get('timed', True))
        except Exception as e:
            r = (None, {'error': str(e)})
        notes, info = r
        if c.get('track'):
            info['track'] = c['track']
        for k in ('usdb_id', 'creator'):
            if c.get(k):
                info[k] = c[k]
        if best[1] is None or (notes is not None, info.get('match', 0)) > (best[0] is not None, best[1].get('match', 0)):
            best = (notes, info)
    return best


# ---------------------------------------------------------------- the video's own pitch bars, with the original's pitch
def bars_with_original(bars, line, vocal, fill=True):
    """The karaoke video's pitch bars ([[start, length, step]] on the karaoke timeline; the steps are only
    relative, read from the bars' heights) made into a guide with the original singer's real pitch:
    each bar takes the pitch the singer holds through it, bars without clear singing take their own step
    moved into the song's key, and stretches the video draws no bars for but the singer sings are filled
    from the vocal guide (vocal, [[start, length, midi]]). Returns (notes, info)."""
    m, dt, go, gk = line['m'], float(line['dt']), line['go'], line['gk']
    to_o = lambda t: float(np.interp(t, gk, go))

    def steady(sh):
        # how much singing sits on one steady note inside the bars, with the bars moved by sh seconds
        n = 0
        for t, d, _ in bars[::2]:
            a, b = to_o(t + sh + 0.15 * d), to_o(t + sh + 0.85 * d)
            v = m[int(a / dt):max(int(a / dt) + 1, int(b / dt))]
            v = v[np.isfinite(v)]
            if len(v) >= 3:
                n += int((np.abs(v - np.median(v)) < 0.5).sum())
        return n
    # some videos draw their bars a little early or late against the sound
    shifts = np.round(np.arange(-0.6, 0.61, 0.05), 2)
    sc = [steady(x) for x in shifts]
    sh = float(shifts[int(np.argmax(sc))]) if max(sc) > 1.03 * sc[len(shifts) // 2] else 0.0
    bars = [[t + sh, d, p] for t, d, p in bars]
    out, diffs = [], []
    for t, d, p in bars:
        a, b = to_o(t + 0.2 * d), to_o(t + 0.8 * d)
        seg = m[int(a / dt):max(int(a / dt) + 1, int(b / dt))]
        v = seg[np.isfinite(seg)]
        if len(seg) and len(v) >= 0.4 * len(seg) and len(v) >= 3:
            q = int(round(float(np.median(v))))
            out.append([t, d, q, True])
            diffs.append(q - p)
        else:
            out.append([t, d, p, False])
    info = {'bars': len(bars), 'from_singer': len(diffs), 'shift': sh}
    if diffs:
        # the bars' steps sit a fixed distance from the real notes: the most common distance, octave aside
        cls = int(np.bincount(np.mod(diffs, 12), minlength=12).argmax())
        shift = int(np.median([x for x in diffs if x % 12 == cls]))
        info['key_shift'] = shift
        sung = [n for n in out if n[3]]
        for n in out:
            if n[3]:
                continue
            q = n[2] + shift
            near = [x[2] for x in sung if abs(x[0] - n[0]) < 6]
            if near:
                q += 12 * int(round((float(np.median(near)) - q) / 12))
            n[2] = q
    out = [[round(t, 3), round(d, 3), int(p)] for t, d, p, _ in out]
    added = 0
    if fill and vocal:
        # what the singer sings where the video draws nothing
        cover = np.zeros(int(max([t + d for t, d, _ in out + vocal] or [0]) * 100) + 2, bool)
        for t, d, _ in out:
            cover[int(t * 100):int((t + d) * 100) + 1] = True
        for t, d, p in vocal:
            seg = cover[int(t * 100):max(int(t * 100) + 1, int((t + d) * 100))]
            if seg.mean() < 0.3:
                out.append([t, d, p])
                added += 1
        out.sort()
    info['filled'] = added
    return out, info


# ---------------------------------------------------------------- the UltraStar song database
def usdb_file(media):
    return os.path.join(media, 'usdb.json')


class Usdb:
    def __init__(self, user, password):
        self.user, self.password = user, password
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) OngakuRenshuu')]

    def page(self, params, data=None):
        url = USDB + 'index.php?' + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        with self.op.open(url, body, timeout=20) as r:
            raw = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
        return raw.decode('utf-8', 'replace')

    def login(self):
        self.page({}, {'user': self.user, 'pass': self.password, 'login': 'Login'})
        prof = self.page({'link': 'profil'})
        if 'logout' not in prof.lower() and self.user.lower() not in prof.lower():
            raise RuntimeError('USDB did not accept that user name and password')
        return True

    def search(self, artist='', title='', limit=30):
        html = self.page({'link': 'list'}, {'interpret': artist, 'title': title, 'order': 'id', 'ud': 'desc',
                                            'limit': str(limit), 'details': '1', 'start': '0'})
        rows = []
        for m in re.finditer(r'<tr class="list_tr\d"\s+data-songid="(\d+)"[^>]*>(.*?)</tr>', html, re.S):
            cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', m.group(2), re.S)]
            # sample, cover, artist, title, genre, year, edition, golden notes, language, creator, rating, views
            if len(cells) >= 10:
                rows.append({'id': int(m.group(1)), 'artist': unesc(cells[2]), 'title': unesc(cells[3]),
                             'language': cells[8], 'creator': unesc(cells[9])})
        return rows

    def txt(self, sid):
        html = self.page({'link': 'gettxt', 'id': str(int(sid))}, {'wd': '1'})
        m = re.search(r'<textarea[^>]*>(.*?)</textarea>', html, re.S)
        if not m:
            raise RuntimeError('USDB did not send the note file')
        return unesc(m.group(1))


def unesc(s):
    import html
    return html.unescape(s)


def usdb_client(media):
    try:
        cfg = json.load(open(usdb_file(media)))
    except (OSError, ValueError):
        return None
    if not cfg.get('user'):
        return None
    c = Usdb(cfg['user'], base64.b64decode(cfg.get('pass', '')).decode())
    c.login()
    return c


def save_login(media, user, password):
    Usdb(user, password).login()
    f = usdb_file(media)
    fd = os.open(f + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        json.dump({'user': user, 'pass': base64.b64encode(password.encode()).decode()}, fh)
    os.replace(f + '.tmp', f)


def norm(s):
    s = re.sub(r'[\(\[（【].*?[\)\]）】]', ' ', s or '')
    return re.sub(r'[\s\W_]+', '', s.lower())


def usdb_candidates(client, titles, artists, want=4):
    """USDB songs whose title matches one of the names the song goes by, best first."""
    seen, found = set(), []
    for t in titles:
        if not t:
            continue
        for row in client.search(title=t):
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            same_title = norm(row['title']) == norm(t) or (norm(t) and norm(t) in norm(row['title']))
            same_artist = any(a and (norm(a) in norm(row['artist']) or norm(row['artist']) in norm(a)) for a in artists)
            if same_title:
                found.append((2 * same_artist + 1, row))
    found.sort(key=lambda x: -x[0])
    return [r for _, r in found[:want]]


# ---------------------------------------------------------------- adding a guide to a song
def apply_guide(song, notes, info, make_default=True):
    """Keep every guide the song has under song['guides'], add the sheet-music one, and make it the default."""
    guides = song.get('guides') or {}
    if 'vocals' not in guides and song.get('guide') == 'vocals' and song.get('notes'):
        guides['vocals'] = {'notes': song['notes'], 'analysis': song.get('analysis')}
    if 'bars' not in guides and song.get('guide', 'bars') == 'bars' and song.get('notes'):
        guides['bars'] = {'notes': song['notes'], 'analysis': song.get('analysis')}
    guides['sheet'] = {'notes': notes, 'analysis': info}
    song['guides'] = guides
    if make_default:
        song['notes'], song['guide'] = notes, 'sheet'
    return song


def add_guide(song_dir, notes, info, make_default=True):
    p = os.path.join(song_dir, 'song.json')
    song = apply_guide(json.load(open(p, encoding='utf-8')), notes, info, make_default)
    tmp = p + '.tmp'
    json.dump(song, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False)
    os.replace(tmp, p)
    return song


def load_line(song_dir):
    f = os.path.join(song_dir, 'work', 'line.npz')
    if not os.path.exists(f):
        raise RuntimeError('this song was imported before sheet-music guides existed; import it again first')
    return dict(np.load(f))


def from_usdb(media, song_dir, names, artists, duration=None):
    client = usdb_client(media)
    if client is None:
        return None, {'error': 'no USDB login'}
    say('sheet', 0.1, 'Searching the UltraStar song database')
    rows = usdb_candidates(client, names, artists)
    if not rows:
        return None, {'error': 'not in USDB'}
    cands = []
    for r in rows:
        try:
            c = parse_ultrastar(client.txt(r['id']))
        except Exception:
            continue
        c.update(usdb_id=r['id'], creator=r['creator'], title=r['title'], artist=r['artist'])
        cands.append(c)
    say('sheet', 0.6, f'Lining up {len(cands)} note file{"s" if len(cands) != 1 else ""} with the singer')
    return best_of(cands, load_line(song_dir), duration)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('song_dir', nargs='?')
    ap.add_argument('--file')
    ap.add_argument('--usdb', action='store_true')
    ap.add_argument('--usdb-login', nargs=2, metavar=('USER', 'PASS'))
    ap.add_argument('--media', default=default_media())
    a = ap.parse_args()
    try:
        if a.usdb_login:
            save_login(a.media, *a.usdb_login)
            say('done', 1, 'Logged in to USDB')
            return
        song = json.load(open(os.path.join(a.song_dir, 'song.json'), encoding='utf-8'))
        if a.file:
            say('sheet', 0.2, 'Reading the file')
            notes, info = best_of(read_file(a.file), load_line(a.song_dir), song.get('duration'))
            info['file'] = os.path.basename(a.file)
        else:
            notes, info = from_usdb(a.media, a.song_dir, *song_names(song), song.get('duration'))
        if notes is None:
            raise RuntimeError(f'it does not follow the singer closely enough ({round(100 * info.get("match", 0))}% of notes)'
                               if 'match' in info else info.get('error', 'nothing was found'))
        add_guide(a.song_dir, notes, info)
        say('done', 1, f'Sheet-music guide added: {len(notes)} notes, {round(100 * info["match"])}% match the singer', info=info)
    except Exception as e:
        msg = str(e)
        say('error', None, msg[:1].upper() + msg[1:])
        sys.exit(1)


def song_names(song):
    """The names a song may be listed under: (titles, artists), from the karaoke video's title (【カラオケ】Song /
    Artist) and the original recording's (Artist - Song (MUSIC VIDEO))."""
    titles, artists = [], []
    br = lambda s: re.sub(r'[【\[（(「][^】\]）)」]*[】\]）)」]', ' ', s or '').strip()
    junk = r'カラオケ|karaoke|노래방|kara-?u|off\s*vocal|instrumental|ガイド(なし|あり|無し)?|on\s*vocal'
    k = re.sub(r'\s+', ' ', re.sub(junk, ' ', br(song.get('title')), flags=re.I)).strip()
    parts = [x.strip() for x in re.split(r'\s*[/／|｜]\s*', k) if x.strip()]
    if len(parts) >= 2:
        titles.append(parts[0])
        artists.append(re.split(r'\s+[-–—]\s+', parts[1])[0])
    else:
        # Artist - Song, or Song - Artist: try both ways round
        dash = [x.strip() for x in re.split(r'\s+[-–—]\s+', k) if x.strip()]
        titles += dash[::-1][:2]
        artists += dash[:2]
    o = br(((song.get('analysis') or {}).get('original') or {}).get('title'))
    parts = [x.strip() for x in re.split(r'\s+[-–—]\s+', o) if x.strip()]
    if len(parts) >= 2:
        artists.append(parts[0])
        titles.append(re.split(r'\s{2,}|[-–—]', parts[1])[0].strip())
    ch = ((song.get('analysis') or {}).get('original') or {}).get('channel') or ''
    if ch and not re.search(r'topic|records|music|entertainment|official', ch, re.I):
        artists.append(ch)
    uniq = lambda xs: [x for i, x in enumerate(xs) if x and x not in xs[:i]]
    return uniq(titles), uniq(artists)


if __name__ == '__main__':
    main()
