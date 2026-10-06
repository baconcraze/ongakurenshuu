#!/usr/bin/env python3
"""Build the karaoke demo songs that ship with Ongaku Renshuu (developer tool, not needed at runtime).

The songs are public domain. Each one gets a backing track rendered with FluidSynth from a General MIDI
SoundFont (a soft guide melody, chords and bass), an exact pitch guide, and lyrics timed syllable by
syllable. Output: karaoke-demos/<id>/{song.json, audio.m4a} and karaoke-demos/index.json.

Usage: tools/make_demo_karaoke.py [--sf2 PATH]   (needs fluidsynth and ffmpeg)
"""
import argparse, json, os, struct, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'karaoke-demos')
NOTE = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def m(name):
    """'A4', 'F#3', 'Bb2' -> MIDI number."""
    n = NOTE[name[0]]
    rest = name[1:]
    if rest.startswith('#'):
        n += 1
        rest = rest[1:]
    elif rest.startswith('b'):
        n -= 1
        rest = rest[1:]
    return (int(rest) + 1) * 12 + n


CHORDS = {'C': ('C', [0, 4, 7]), 'F': ('F', [0, 4, 7]), 'G': ('G', [0, 4, 7]), 'G7': ('G', [0, 4, 10]),
          'D': ('D', [0, 4, 7]), 'D7': ('D', [0, 4, 10]), 'Em': ('E', [0, 3, 7]), 'Am': ('A', [0, 3, 7]),
          'C/G': ('G', [5, 9, 12]), 'Dm': ('D', [0, 3, 7])}

# Each line: list of (syllable text including its trailing space or punctuation, [(note, beats), ...]).
# A note of None is a rest. Lines follow each other without gaps unless a rest is written.
SONGS = [
    {
        'id': 'demo-sakura', 'title': 'さくらさくら (Sakura Sakura)', 'artist': 'Traditional Japanese song (public domain)',
        'bpm': 66, 'beats': 4, 'intro_bars': 2, 'guide': 77, 'chord_prog': 107, 'bass_prog': 107, 'style': 'koto',
        'lines': [
            [('さ', [('A4', 1)]), ('く', [('A4', 1)]), ('ら ', [('B4', 2)]), ('さ', [('A4', 1)]), ('く', [('A4', 1)]), ('ら', [('B4', 2)])],
            [('や', [('A4', 1)]), ('よ', [('B4', 1)]), ('い', [('C5', 1)]), ('の ', [('B4', 1)]), ('そ', [('A4', 1)]), ('ら', [('B4', .5), ('A4', .5)]), ('は', [('F4', 2)])],
            [('み', [('E4', 1)]), ('わ', [('C4', 1)]), ('た', [('E4', 1)]), ('す ', [('F4', 1)]), ('か', [('E4', 1)]), ('ぎ', [('E4', .5), ('C4', .5)]), ('り', [('B3', 2)])],
            [('か', [('A4', 1)]), ('す', [('B4', 1)]), ('み', [('C5', 1)]), ('か ', [('B4', 1)]), ('く', [('A4', 1)]), ('も', [('B4', .5), ('A4', .5)]), ('か', [('F4', 2)])],
            [('に', [('E4', 1)]), ('お', [('C4', 1)]), ('い', [('E4', 1)]), ('ぞ ', [('F4', 1)]), ('い', [('E4', 1)]), ('ず', [('E4', .5), ('C4', .5)]), ('る', [('B3', 2)])],
            [('い', [('A4', 1)]), ('ざ', [('A4', 1)]), ('や ', [('B4', 2)]), ('い', [('A4', 1)]), ('ざ', [('A4', 1)]), ('や', [('B4', 2)])],
            [('み', [('E4', 1)]), ('に', [('F4', 1)]), ('ゆ', [('B4', .5), ('A4', .5)]), ('か', [('F4', 1)]), ('ん', [('E4', 4)])],
        ],
    },
    {
        'id': 'demo-amazing-grace', 'title': 'Amazing Grace', 'artist': 'John Newton, tune New Britain (public domain)',
        'bpm': 76, 'beats': 3, 'intro_bars': 2, 'pickup': 1, 'guide': 73, 'chord_prog': 0, 'bass_prog': 32, 'style': 'waltz',
        'chords': ['G', 'G', 'C', 'G', 'G', 'D', 'D', 'D', 'G', 'G7', 'C', 'G', 'G', 'D', 'G', 'G'],
        'lines': [
            [('A', [('D4', 1)]), ('ma', [('G4', 2)]), ('zing ', [('B4', .5), ('G4', .5)]), ('grace, ', [('B4', 2)]), ('how ', [('A4', 1)]), ('sweet ', [('G4', 2)]), ('the ', [('E4', 1)]), ('sound', [('D4', 2)])],
            [('that ', [('D4', 1)]), ('saved ', [('G4', 2)]), ('a ', [('B4', .5), ('G4', .5)]), ('wretch ', [('B4', 2)]), ('like ', [('A4', .5), ('B4', .5)]), ('me.', [('D5', 5)])],
            [('I ', [('B4', 1)]), ('once ', [('D5', 2)]), ('was ', [('B4', .5), ('G4', .5)]), ('lost, ', [('B4', 2)]), ('but ', [('A4', 1)]), ('now ', [('G4', 2)]), ('am ', [('E4', 1)]), ('found,', [('D4', 2)])],
            [('was ', [('D4', 1)]), ('blind, ', [('G4', 2)]), ('but ', [('B4', .5), ('G4', .5)]), ('now ', [('B4', 2)]), ('I ', [('A4', 1)]), ('see.', [('G4', 5)])],
        ],
    },
    {
        'id': 'demo-ode-to-joy', 'title': 'Ode to Joy (Freude, schöner Götterfunken)', 'artist': 'Ludwig van Beethoven, words by Friedrich Schiller (public domain)',
        'bpm': 100, 'beats': 4, 'intro_bars': 2, 'guide': 71, 'chord_prog': 0, 'bass_prog': 32, 'style': 'march',
        'lines': [
            [('Freu', [('E4', 1)]), ('de, ', [('E4', 1)]), ('schö', [('F4', 1)]), ('ner ', [('G4', 1)]), ('Göt', [('G4', 1)]), ('ter', [('F4', 1)]), ('fun', [('E4', 1)]), ('ken,', [('D4', 1)])],
            [('Toch', [('C4', 1)]), ('ter ', [('C4', 1)]), ('aus ', [('D4', 1)]), ('E', [('E4', 1)]), ('ly', [('E4', 1.5)]), ('si', [('D4', .5)]), ('um,', [('D4', 2)])],
            [('Wir ', [('E4', 1)]), ('be', [('E4', 1)]), ('tre', [('F4', 1)]), ('ten ', [('G4', 1)]), ('feu', [('G4', 1)]), ('er', [('F4', 1)]), ('trun', [('E4', 1)]), ('ken,', [('D4', 1)])],
            [('Himm', [('C4', 1)]), ('li', [('C4', 1)]), ('sche, ', [('D4', 1)]), ('dein ', [('E4', 1)]), ('Hei', [('D4', 1.5)]), ('lig', [('C4', .5)]), ('tum!', [('C4', 2)])],
            [('Dei', [('D4', 1)]), ('ne ', [('D4', 1)]), ('Zau', [('E4', 1)]), ('ber ', [('C4', 1)]), ('bin', [('D4', 1)]), ('den ', [('E4', .5), ('F4', .5)]), ('wie', [('E4', 1)]), ('der,', [('C4', 1)])],
            [('was ', [('D4', 1)]), ('die ', [('E4', .5), ('F4', .5)]), ('Mo', [('E4', 1)]), ('de ', [('D4', 1)]), ('streng ', [('C4', 1)]), ('ge', [('D4', 1)]), ('teilt;', [('G3', 2)])],
            [('al', [('E4', 1)]), ('le ', [('E4', 1)]), ('Men', [('F4', 1)]), ('schen ', [('G4', 1)]), ('wer', [('G4', 1)]), ('den ', [('F4', 1)]), ('Brü', [('E4', 1)]), ('der,', [('D4', 1)])],
            [('wo ', [('C4', 1)]), ('dein ', [('C4', 1)]), ('sanf', [('D4', 1)]), ('ter ', [('E4', 1)]), ('Flü', [('D4', 1.5)]), ('gel ', [('C4', .5)]), ('weilt.', [('C4', 2)])],
        ],
    },
]
# Ode to Joy harmony, one chord per bar: A A B A' sections.
SONGS[2]['chords'] = ['C', 'G', 'C', 'G', 'C', 'G', 'C', 'C', 'G', 'C', 'G', 'G', 'C', 'G', 'C', 'C']


# ---------------------------------------------------------------- timing
def layout(song):
    """Place every note in time. Returns notes [(start_s, dur_s, midi)], lyric lines and the song length."""
    spb = 60 / song['bpm']
    start_beats = song['intro_bars'] * song['beats'] - song.get('pickup', 0)
    b = start_beats
    notes, lines = [], []
    for li, line in enumerate(song['lines']):
        syl_times = []
        for text, parts in line:
            s0 = b
            for name, beats in parts:
                if name:
                    notes.append((b * spb, beats * spb * 0.95, m(name)))
                b += beats
            syl_times.append((text, s0 * spb, b * spb))
        lines.append(syl_times)
    total_beats = b + song['beats']
    return notes, lines, total_beats * spb, start_beats


def lyric_entries(lines):
    """Two lines on screen at a time, wiping syllable by syllable."""
    out = []
    for i, syl in enumerate(lines):
        text = ''.join(t for t, _, _ in syl).strip()
        n = sum(len(t) for t, _, _ in syl) or 1
        wipe, done = [], 0
        for t, a, b in syl:
            wipe.append([round(a, 3), round(done / n, 4)])
            done += len(t)
            wipe.append([round(b, 3), round(done / n, 4)])
        start, end = syl[0][1], syl[-1][2]
        # two rows that take turns: a line appears when the line two before it (same row) has finished
        first = lines[0][0][1]
        t0 = max(0.0, first - 3.0) if i < 2 else lines[i - 2][-1][2] + 0.6
        out.append({'t0': round(min(t0, start - 0.5), 3), 't1': round(end + 0.6, 3), 's0': round(start, 3),
                    's1': round(end, 3), 'wipe': wipe, 'y': i % 2, 'text': text})
    return out


# ---------------------------------------------------------------- MIDI writing (format 1, 480 ticks per beat)
def vlq(n):
    out = [n & 0x7f]
    n >>= 7
    while n:
        out.insert(0, (n & 0x7f) | 0x80)
        n >>= 7
    return bytes(out)


def track(events):
    events.sort(key=lambda e: (e[0], e[1]))
    data, t = b'', 0
    for tick, order, raw in events:
        data += vlq(tick - t) + raw
        t = tick
    data += vlq(0) + b'\xff\x2f\x00'
    return b'MTrk' + struct.pack('>I', len(data)) + data


def build_midi(song, notes, total_s):
    TPB = 480
    spb = 60 / song['bpm']
    tk = lambda sec: int(round(sec / spb * TPB))
    tempo = [(0, 0, b'\xff\x51\x03' + int(spb * 1e6).to_bytes(3, 'big')),
             (0, 0, b'\xff\x58\x04' + bytes([song['beats'], 2, 24, 8]))]
    on = lambda t, ch, n, v: (t, 1, bytes([0x90 | ch, n, v]))
    off = lambda t, ch, n: (t, 0, bytes([0x80 | ch, n, 0]))
    guide = [(0, 0, bytes([0xC0, song['guide']])), (0, 0, bytes([0xB0, 7, 70]))]
    for s, d, n in notes:
        guide += [on(tk(s), 0, n, 62), off(tk(s + d), 0, n)]
    acc = [(0, 0, bytes([0xC1, song['chord_prog']])), (0, 0, bytes([0xB1, 7, 92])),
           (0, 0, bytes([0xC2, song['bass_prog']])), (0, 0, bytes([0xB2, 7, 100]))]
    beats, bar = song['beats'], song['beats'] * TPB
    nbars = int(total_s / spb / beats) + 1
    for bi in range(nbars):
        t0 = bi * bar
        if song['style'] == 'koto':
            # a gentle plucked figure on the open fifth A and E, with the B and C of the scale
            fig = [(0, ['A2', 'E3']), (1, ['A3']), (1.5, ['B3']), (2, ['E3', 'A3']), (3, ['C4']), (3.5, ['B3'])]
            for beat, names in fig:
                for nm in names:
                    tt = t0 + int(beat * TPB)
                    acc += [on(tt, 1, m(nm), 70), off(tt + TPB, 1, m(nm))]
            continue
        prog = song['chords']
        ci = bi - song['intro_bars']
        name = prog[ci % len(prog)] if ci >= 0 else prog[0]
        root, iv = CHORDS[name]
        r = m(root + '3')
        if r > m('E3'):
            r -= 12
        triad = [r + 12 + x for x in iv]
        bass = r - 12 if r - 12 >= m('E1') else r
        if song['style'] == 'waltz':
            acc += [on(t0, 2, bass, 92), off(t0 + TPB, 2, bass)]
            for beat in (1, 2):
                for n in triad:
                    acc += [on(t0 + beat * TPB, 1, n, 58), off(t0 + beat * TPB + int(TPB * 0.9), 1, n)]
        else:
            for beat in (0, 2):
                acc += [on(t0 + beat * TPB, 2, bass if beat == 0 else bass + 7, 92), off(t0 + beat * TPB + int(TPB * 0.95), 2, bass if beat == 0 else bass + 7)]
            for beat in (1, 3):
                for n in triad:
                    acc += [on(t0 + beat * TPB, 1, n, 60), off(t0 + beat * TPB + int(TPB * 0.8), 1, n)]
    head = b'MThd' + struct.pack('>IHHH', 6, 1, 3, TPB)
    return head + track(tempo) + track(guide) + track(acc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sf2', default=os.path.expanduser('~/.local/share/ongaku-renshuu/soundfonts/GeneralUser-GS.sf2'))
    a = ap.parse_args()
    sf2 = a.sf2 if os.path.exists(a.sf2) else os.path.join(ROOT, 'soundfonts', 'sonivox.sf2')
    os.makedirs(OUT, exist_ok=True)
    index = []
    for song in SONGS:
        notes, lines, total, _ = layout(song)
        d = os.path.join(OUT, song['id'])
        os.makedirs(d, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            mid, wav = os.path.join(tmp, 's.mid'), os.path.join(tmp, 's.wav')
            open(mid, 'wb').write(build_midi(song, notes, total))
            subprocess.check_call(['fluidsynth', '-ni', '-g', '0.7', '-r', '44100', '-F', wav, sf2, mid],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(['ffmpeg', '-loglevel', 'error', '-y', '-i', wav, '-t', f'{total + 1.5:.2f}',
                                   '-af', 'afade=t=out:st=%.2f:d=1.5' % total, '-c:a', 'aac', '-b:a', '96k',
                                   os.path.join(d, 'audio.m4a')])
        js = {'version': 1, 'id': song['id'], 'demo': True, 'audio_only': True, 'title': song['title'],
              'artist': song['artist'], 'duration': round(total + 1.5, 2), 'video': 'audio.m4a',
              'notes': [[round(s, 3), round(dd, 3), n] for s, dd, n in notes], 'lyrics': lyric_entries(lines),
              'analysis': {'layout': 'demo'}, 'key_source': 'written'}
        json.dump(js, open(os.path.join(d, 'song.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        index.append({'id': song['id'], 'title': song['title'], 'artist': song['artist'], 'duration': js['duration'],
                      'notes': len(notes), 'lyrics': len(js['lyrics']), 'demo': True,
                      'video': f'karaoke-demos/{song["id"]}/audio.m4a', 'song': f'karaoke-demos/{song["id"]}/song.json'})
        print(f'{song["id"]}: {len(notes)} notes, {total:.0f} s, rendered with {os.path.basename(sf2)}')
    json.dump(index, open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
