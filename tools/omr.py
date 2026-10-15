#!/usr/bin/env python3
"""Read printed sheet music from images with Audiveris (optical music recognition), for Ongaku Renshuu.

Audiveris (https://github.com/Audiveris/audiveris, AGPL-3.0) is downloaded once into MEDIA/audiveris; its
Linux package carries its own Java runtime, so nothing is installed system-wide.

Each page is read at three sizes, since a different size makes different mistakes, and every bar keeps the
version most of the readings agree on (a bar whose notes add up to its time signature wins a tie). Pages are
then joined into one MusicXML score.

Usage: omr.py --setup [--media DIR]
       omr.py PAGE.png [PAGE2.png ...] --out score.musicxml [--media DIR]
Progress is printed as JSON lines, like the video importers.
"""
import argparse, glob, io, json, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request, zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

VERSION = '5.11.0'
DEB = f'https://github.com/Audiveris/audiveris/releases/download/{VERSION}/Audiveris-{VERSION}-ubuntu24.04-x86_64.deb'
SCALES = (1.0, 1.5, 2.0)
# Audiveris normally skips a staff much shorter than the others, such as a last line holding one or two bars
CONSTANTS = ['org.audiveris.omr.sheet.grid.LinesRetriever.minStaffLength=10',
             'org.audiveris.omr.sheet.grid.ClustersRetriever.minClusterLengthRatio=0.1',
             'org.audiveris.omr.sheet.grid.LinesRetriever.minTrueLengthRatio=0.1']


def say(**k):
    print(json.dumps(k, ensure_ascii=False), flush=True)


def default_media():
    return os.environ.get('ONGAKU_MEDIA') or os.path.join(
        os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'), 'ongaku-renshuu-media')


def binary(media):
    b = os.path.join(media, 'audiveris', 'opt', 'audiveris', 'bin', 'Audiveris')
    return b if os.access(b, os.X_OK) else None


def setup(media):
    """Download the Audiveris package and unpack it (a .deb is an ar archive holding data.tar.zst)."""
    if binary(media):
        say(stage='setup', progress=1, message='Audiveris is already installed')
        return
    if sys.platform != 'linux':
        raise SystemExit('Reading sheet music on this computer needs Linux; on other systems use the Anthropic API reader.')
    dest = os.path.join(media, 'audiveris')
    tmp = dest + '.part'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    say(stage='setup', progress=0.02, message='Downloading Audiveris (about 80 MB)')
    with urllib.request.urlopen(DEB, timeout=60) as r:
        total = int(r.headers.get('Content-Length') or 0)
        buf = io.BytesIO()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            buf.write(chunk)
            if total:
                say(stage='setup', progress=round(0.02 + 0.85 * buf.tell() / total, 3), message='Downloading Audiveris')
    data = buf.getvalue()
    if data[:8] != b'!<arch>\n':
        raise SystemExit('The Audiveris download is not a Debian package.')
    pos, member = 8, None
    while pos + 60 <= len(data):
        name = data[pos:pos + 16].decode().strip().rstrip('/')
        size = int(data[pos + 48:pos + 58].decode().strip())
        if name.startswith('data.tar'):
            member = (name, data[pos + 60:pos + 60 + size])
            break
        pos += 60 + size + (size & 1)
    if not member:
        raise SystemExit('The Audiveris package has no data archive.')
    say(stage='setup', progress=0.9, message='Unpacking Audiveris')
    name, blob = member
    if name.endswith('.zst'):
        try:
            from compression import zstd          # Python 3.14+
            blob = zstd.decompress(blob)
        except ImportError:
            z = shutil.which('zstd')
            if not z:
                raise SystemExit('Unpacking Audiveris needs Python 3.14 or the zstd program (sudo pacman -S zstd).')
            blob = subprocess.run([z, '-dc'], input=blob, capture_output=True, check=True).stdout
        mode = 'r:'
    else:
        mode = 'r:*'
    with tarfile.open(fileobj=io.BytesIO(blob), mode=mode) as tf:
        tf.extractall(tmp, filter='tar')
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    if not binary(media):
        raise SystemExit('Audiveris was unpacked but its program is missing.')
    say(stage='setup', progress=1, message='Audiveris is ready')


def run_audiveris(exe, image, outdir):
    os.makedirs(outdir, exist_ok=True)
    cmd = [exe, '-batch', '-export', '-output', outdir]
    for c in CONSTANTS:
        cmd += ['-constant', c]
    subprocess.run(cmd + ['--', image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    found = glob.glob(os.path.join(outdir, '*.mxl'))
    if not found:
        return None
    with zipfile.ZipFile(found[0]) as z:
        names = [n for n in z.namelist() if n.endswith('.xml') and not n.startswith('META-INF')]
        return ET.fromstring(z.read(names[0])) if names else None


def scaled(image, scale, path):
    if scale == 1.0:
        shutil.copy(image, path)
        return path
    # ffmpeg is already needed for the video imports; Lanczos keeps the note heads round
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', image, '-vf', f'scale=iw*{scale}:ih*{scale}:flags=lanczos', path], check=True)
    return path


# ---- comparing and voting on bars
STEPS = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def lcm(a, b):
    from math import gcd
    return a * b // gcd(a, b)


def divisions_of(root):
    return [int(d.text) for d in root.iter('divisions') if d.text and d.text.strip().isdigit()]


def rescale(root, to):
    """Give every duration in the score the same divisions per quarter note."""
    for part in root.iter('part'):
        div = None
        for m in part.findall('measure'):
            for a in m.findall('attributes'):
                d = a.find('divisions')
                if d is not None and d.text:
                    div = int(d.text)
                    d.text = str(to)
            if not div or div == to:
                continue
            for el in m.iter():
                if el.tag in ('note', 'backup', 'forward'):
                    du = el.find('duration')
                    if du is not None and du.text:
                        du.text = str(int(Fraction(int(du.text) * to, div)))


def measure_sig(m):
    """What a bar says: its signatures and every note's staff, start, pitch and length."""
    out, t, last = [], 0, 0
    for a in m.findall('attributes'):
        tm = a.find('time')
        if tm is not None:
            out.append(('time', tm.findtext('beats'), tm.findtext('beat-type')))
        k = a.find('key')
        if k is not None:
            out.append(('key', k.findtext('fifths')))
    for el in m:
        if el.tag == 'backup':
            t -= int(el.findtext('duration') or 0)
        elif el.tag == 'forward':
            t += int(el.findtext('duration') or 0)
        elif el.tag == 'note':
            if el.find('grace') is not None:
                continue
            d = int(el.findtext('duration') or 0)
            start = last if el.find('chord') is not None else t
            p = el.find('pitch')
            pitch = 'r' if p is None else (int(p.findtext('octave')) + 1) * 12 + STEPS[p.findtext('step')] + int(float(p.findtext('alter') or 0))
            out.append(('n', el.findtext('staff') or '1', start, pitch, d))
            if el.find('chord') is None:
                last = t
                t += d
    return tuple(sorted(out, key=str))


def full(m, time_len):
    """True when every voice in the bar fills exactly the bar's time signature."""
    if not time_len:
        return False
    per = Counter()
    for el in m:
        if el.tag == 'note' and el.find('chord') is None and el.find('grace') is None:
            per[(el.findtext('staff') or '1', el.findtext('voice') or '1')] += int(el.findtext('duration') or 0)
    return bool(per) and all(v == time_len for v in per.values())


TYPES = [(4, 'whole', 0), (3, 'half', 1), (2, 'half', 0), (Fraction(3, 2), 'quarter', 1), (1, 'quarter', 0), (Fraction(3, 4), 'eighth', 1),
         (Fraction(1, 2), 'eighth', 0), (Fraction(3, 8), '16th', 1), (Fraction(1, 4), '16th', 0), (Fraction(1, 8), '32nd', 0)]


def rest_el(dur, div, voice, staff):
    """A rest of dur divisions, written as the longest standard note value that fits (the caller loops)."""
    q = Fraction(dur, div)
    for length, typ, dots in TYPES:
        if length <= q:
            n = ET.Element('note')
            ET.SubElement(n, 'rest')
            ET.SubElement(n, 'duration').text = str(int(length * div))
            ET.SubElement(n, 'voice').text = voice
            ET.SubElement(n, 'type').text = typ
            for _ in range(dots):
                ET.SubElement(n, 'dot')
            if staff:
                ET.SubElement(n, 'staff').text = staff
            return n, int(length * div)
    return None, 0


def infer_time(root):
    """No time signature read at all: give the score the bar length most bars actually have."""
    if root.find('.//time') is not None:
        return
    div, lens = None, Counter()
    for part in root.iter('part'):
        for m in part.findall('measure'):
            d = m.find('attributes/divisions')
            if d is not None and d.text:
                div = int(d.text)
            per = Counter()
            for el in m:
                if el.tag == 'note' and el.find('chord') is None and el.find('grace') is None:
                    per[(el.findtext('staff') or '1', el.findtext('voice') or '1')] += int(el.findtext('duration') or 0)
            if per and div:
                lens[max(per.values())] += 1
    if not lens or not div:
        return
    q = Fraction(lens.most_common(1)[0][0], div)          # quarters per bar
    beats, typ = (q, 4) if q.denominator == 1 else (q * 2, 8) if (q * 2).denominator == 1 else (q * 4, 16)
    if beats.denominator != 1 or beats < 1:
        return
    part = root.find('part')
    m = part.find('measure') if part is not None else None
    if m is None:
        return
    attrs = m.find('attributes')
    if attrs is None:
        attrs = ET.Element('attributes')
        m.insert(0, attrs)
    t = ET.Element('time')
    ET.SubElement(t, 'beats').text = str(int(beats))
    ET.SubElement(t, 'beat-type').text = str(typ)
    # time goes after divisions and key, before staves and clefs
    kids = list(attrs)
    at = next((i for i, k in enumerate(kids) if k.tag not in ('footnote', 'level', 'divisions', 'key')), len(kids))
    attrs.insert(at, t)


def add_dot(m, note, extra):
    """Lengthen a note and the rest of its chord by half, as a dotted note."""
    kids = list(m)
    i = kids.index(note)
    group = [note] + [k for k in kids[i + 1:] if k.tag == 'note' and k.find('chord') is not None][:0]
    j = i + 1
    while j < len(kids) and kids[j].tag == 'note' and kids[j].find('chord') is not None:
        group.append(kids[j])
        j += 1
    for n in group:
        du = n.find('duration')
        du.text = str(int(du.text) + extra)
        typ = n.find('type')
        dot = ET.Element('dot')
        if typ is not None:
            n.insert(list(n).index(typ) + 1, dot)
        else:
            n.append(dot)


def fill_short_voices(root):
    """A voice that comes up short of its bar has lost a rest. Put rests back where its notes stand further
    right than their timing explains, judged against the notes of the full voices in the same bar."""
    for part in root.iter('part'):
        div, bar = None, None
        for m in part.findall('measure'):
            for a in m.findall('attributes'):
                d = a.find('divisions')
                if d is not None and d.text:
                    div = int(d.text)
                tm = a.find('time')
                if tm is not None and (tm.findtext('beats') or '').isdigit() and div:
                    bar = int(div * 4 * int(tm.findtext('beats')) / int(tm.findtext('beat-type')))
            if not div or not bar:
                continue
            voices, t, last = {}, 0, 0
            for el in m:
                if el.tag == 'backup':
                    t -= int(el.findtext('duration') or 0)
                elif el.tag == 'forward':
                    t += int(el.findtext('duration') or 0)
                elif el.tag == 'note' and el.find('grace') is None:
                    start = last if el.find('chord') is not None else t
                    if el.find('chord') is None:
                        key = (el.findtext('staff') or '1', el.findtext('voice') or '1')
                        voices.setdefault(key, []).append((el, start))
                        last = t
                        t += int(el.findtext('duration') or 0)
            total = {k: sum(int(e.findtext('duration') or 0) for e, _ in v) for k, v in voices.items()}
            # only a bar that otherwise makes sense: no voice too long, none running across both staves
            if any(x > bar for x in total.values()) or len({k[1] for k in voices}) != len(voices):
                continue
            anchors = sorted({(float(e.get('default-x')), st) for k, v in voices.items() if total[k] == bar
                              for e, st in v if e.get('default-x')})
            width = float(m.get('width') or 0)
            for k, v in voices.items():
                short = bar - total[k]
                if short <= 0 or short % 1:
                    continue
                pts = [(0.0, 0)] + [a for a in anchors] + ([(width, bar)] if width else [])
                def at(x):
                    lo = max((p for p in pts if p[0] <= x), default=pts[0])
                    hi = min((p for p in pts if p[0] > x), default=None)
                    if hi is None or hi[0] == lo[0]:
                        return lo[1]
                    return lo[1] + (hi[1] - lo[1]) * (x - lo[0]) / (hi[0] - lo[0])
                cur, inserts, prev = 0, [], None
                for e, _ in v:
                    x = e.get('default-x')
                    if x is not None and len(pts) > 2:
                        want = at(float(x))
                        unit = max(1, div // 4)          # a sixteenth, or the smallest step the score counts in
                        gap = min(short, round((want - cur) / unit) * unit)
                        if gap > 0:
                            pd = int(prev.findtext('duration') or 0) if prev is not None else 0
                            if prev is not None and prev.find('rest') is None and prev.find('dot') is None and gap * 2 == pd:
                                add_dot(m, prev, gap)        # the dot after the note before was missed
                            else:
                                inserts.append((e, gap))
                            short -= gap
                            cur += gap
                    cur += int(e.findtext('duration') or 0)
                    prev = e
                for e, gap in inserts:
                    idx = list(m).index(e)
                    while gap > 0:
                        r, used = rest_el(gap, div, k[1], k[0] if e.find('staff') is not None else None)
                        if not r:
                            break
                        m.insert(idx, r)
                        idx += 1
                        gap -= used
                if short > 0:
                    # the rest is at the end; the bar's last note of this voice is followed by it
                    lastel = v[-1][0]
                    idx = list(m).index(lastel) + 1
                    while idx < len(m) and m[idx].tag == 'note' and m[idx].find('chord') is not None:
                        idx += 1
                    while short > 0:
                        r, used = rest_el(short, div, k[1], k[0] if lastel.find('staff') is not None else None)
                        if not r:
                            break
                        m.insert(idx, r)
                        idx += 1
                        short -= used
                grown = bar - total[k] - short
                if grown:
                    # the step back to the next voice now has further to go
                    after = list(m).index(v[-1][0]) + 1
                    while after < len(m) and m[after].tag != 'backup' and not (
                            m[after].tag == 'note' and (m[after].findtext('voice') or '1') != k[1]):
                        after += 1
                    if after < len(m) and m[after].tag == 'backup':
                        du = m[after].find('duration')
                        du.text = str(int(du.text) + grown)


def repair(root):
    if root is not None:
        infer_time(root)
        fill_short_voices(root)
    return root


def vote(readings):
    """Build one score from several readings of the same page, bar by bar."""
    readings = [r for r in readings if r is not None]
    if len(readings) < 2:
        return readings[0] if readings else None
    div = 1
    for r in readings:
        for d in divisions_of(r):
            div = lcm(div, d)
    for r in readings:
        rescale(r, div)
    shape = lambda r: tuple(len(p.findall('measure')) for p in r.findall('part'))
    common = Counter(shape(r) for r in readings).most_common(1)[0][0]
    same = [r for r in readings if shape(r) == common]
    base = same[0]
    for pi, part in enumerate(base.findall('part')):
        parts = [r.findall('part')[pi] for r in same]
        bars = [p.findall('measure') for p in parts]
        time_len = None
        for mi in range(len(bars[0])):
            # the time signature in force, from the base reading
            for a in bars[0][mi].findall('attributes'):
                tm = a.find('time')
                if tm is not None and (tm.findtext('beats') or '').isdigit():
                    time_len = int(div * 4 * int(tm.findtext('beats')) / int(tm.findtext('beat-type')))
            sigs = [measure_sig(b[mi]) for b in bars]
            counts = Counter(sigs)
            ranked = sorted(range(len(bars)), key=lambda i: (-counts[sigs[i]], not full(bars[i][mi], time_len), i))
            pick = ranked[0]
            if pick != 0:
                old = bars[0][mi]
                new = bars[pick][mi]
                idx = list(part).index(old)
                part.remove(old)
                part.insert(idx, new)
                bars[0][mi] = new
    return base


def join_pages(scores):
    """Append the bars of each later page to the first page's parts, numbering them on."""
    scores = [s for s in scores if s is not None]
    if not scores:
        return None
    first = scores[0]
    parts = first.findall('part')
    for s in scores[1:]:
        for pi, p in enumerate(s.findall('part')):
            if pi >= len(parts):
                break
            for m in p.findall('measure'):
                parts[pi].append(m)
    for p in parts:
        for i, m in enumerate(p.findall('measure')):
            m.set('number', str(i + 1))
    return first


def recognize(media, images, out):
    exe = binary(media)
    if not exe:
        raise SystemExit('Audiveris is not installed yet.')
    work = tempfile.mkdtemp(prefix='ongaku-omr-')
    try:
        pages = []
        for n, img in enumerate(images):
            say(stage='omr', progress=round(0.05 + 0.9 * n / len(images), 3), message=f'Reading page {n + 1} of {len(images)}')
            jobs = []
            for s in SCALES:
                d = os.path.join(work, f'p{n}-x{s}')
                os.makedirs(d)
                jobs.append((scaled(img, s, os.path.join(d, f'page{n + 1}.png')), os.path.join(d, 'out')))
            with ThreadPoolExecutor(len(jobs)) as ex:
                readings = [repair(r) for r in ex.map(lambda j: run_audiveris(exe, *j), jobs)]
            pages.append(vote(readings))
        score = join_pages(pages)
        if score is None:
            raise SystemExit('No music could be read from the image. Try a sharper, straighter picture.')
        bars = max((len(p.findall('measure')) for p in score.findall('part')), default=0)
        with open(out, 'wb') as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(ET.tostring(score, encoding='utf-8'))
        say(stage='done', progress=1, message=f'Read {bars} bars', bars=bars)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('images', nargs='*')
    ap.add_argument('--setup', action='store_true', help='download and unpack Audiveris')
    ap.add_argument('--out', help='MusicXML file to write')
    ap.add_argument('--media', default=default_media())
    a = ap.parse_args()
    if a.setup:
        setup(a.media)
        return
    if not a.images or not a.out:
        ap.error('give one or more page images and --out')
    recognize(a.media, a.images, a.out)


if __name__ == '__main__':
    main()
