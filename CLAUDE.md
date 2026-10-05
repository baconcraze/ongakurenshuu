# Ongaku Renshuu (音楽練習)

A free, offline-first practice player for guitar and bass tab and piano parts. It runs as a static web page served from 127.0.0.1 by a tiny Python server. There is no build step, no bundler, and no package.json: edit the files and reload the browser.

## Layout

- `index.html` holds the whole app: CSS, markup, and one `'use strict'` IIFE. Sections start with `/* ---------- Name ---------- */` comments, in this order: Tunings, Demo songs, State, Parser (ASCII tab), Note text (piano), Audio (pluck synth), Transport, Rendering (tab view), Loop, Songs (load, edit, save, tracks), UI wiring, Guitar Pro import (also MusicXML through alphaTab, and the MIDI reader), Image import, SoundFont player, Instrument sound settings, YouTube play-along, Sheet music (VexFlow), Tab or sheet music view, Piano keyboard, ABC notation, Video imports (server API, piano-from-video), Karaoke, Start. Keep new code in the matching section, and add new sections just before Start.
- `ongaku-renshuu.sh` is the launcher (`ongaku` command): background `tools/server.py` on port 8765 (plain `python3 -m http.server` if tools/ is missing), `--karaoke URL` / `--piano URL` import videos from the command line, writes `soundfonts/index.json`, `--get-soundfont` downloads GeneralUser GS.
- `install.sh` and `update.sh` install to `~/.local/share/ongaku-renshuu`, link `ongaku` and `ongaku-update` into `~/.local/bin`, and add a desktop entry. `update.sh` keeps the last 3 backups and supports `--rollback`.
- `vendor/`: unmodified third-party builds (alphaTab for Guitar Pro files, VexFlow with Bravura for notation). Loaded lazily with a script tag and a jsDelivr fallback. Never edit these; to upgrade, replace the file, keep its license next to it, and update `THIRD_PARTY_NOTICES.md`.
- `soundfonts/`: only `sonivox.sf2` is tracked. Downloaded or user fonts and `index.json` are git-ignored.
- `tools/server.py`: the launcher's web server (replaces `python3 -m http.server`). Serves the app, serves the media folder at `/media/` with byte ranges, and runs imports one at a time at low priority. API: `GET api/info`, `GET api/library`, `GET api/jobs`, `POST api/import {kind:'karaoke'|'piano', url}`, `DELETE api/media/<kind>/<id>`. Write calls need the `X-Ongaku: 1` header and a 127.0.0.1/localhost Host.
- `tools/karaoke.py` and `tools/pianovideo.py`: video importers (numpy, ffmpeg, yt-dlp; Ollama optional for lyric text). They print JSON progress lines and write `MEDIA/<kind>/<id>/song.json` plus `video.mp4`. MEDIA is `${ONGAKU_MEDIA:-~/.local/share/ongaku-renshuu-media}`; its `venv/` holds an up-to-date yt-dlp, installed automatically when YouTube blocks the system one.
- `tools/check.sh`: syntax checks. Run it after every change.

## Data model

- A song: `{id, title, artist, bpm, cpb, kind, fmt?, tuning, tab, tracks?, track?, video?}`. `kind` is `'tab'` (ASCII tab in `tab`, `tuning` is a preset key or an array of MIDI numbers, highest string first) or `'notes'` (piano or melody, `tuning` null). For `'notes'`, `fmt: 'abc'` means `tab` holds ABC notation (the default for new piano songs); without `fmt` it holds the older simple note text. ABC is read by `abcToNoteText()` and then `parseNotes()`, and `noteTextToAbc()` writes it. `cpb` is grid columns per beat; for note songs it is derived from the text on parse.
- Multi-track songs keep every track in `tracks[]` (`{name, kind, fmt?, cpb, tuning, tab}`); the top-level fields mirror the current track, and `syncTrack()` writes edits back.
- `parsed = {kind, steps[], measures[{start,len}], warnings[]}`. A step is one grid slot. Tab notes are `{s, fret, dead?, legato?}`; note-text notes are `{midi, dur, voice, staff}`. `noteMidi(n)` turns either into a pitch.
- Timing: `stepDur()` is seconds per step at the current speed. The Web Audio scheduler (`tick`) runs ahead of `ctx.currentTime`; the cursor is drawn from the scheduled `timeline`. In video mode the YouTube player is the clock instead (`videoTick`, `vtNow`).
- Both views (tab and VexFlow notation) fill the same arrays: `rowEls`, `rowsMeta`, `stepX`, `stepNext`, `stepRow`, `rowGeo`, `noteGroups` (elements with `data-k`), `bandEls`, `mnEls`. `setCursor`, looping, and click-to-seek only rely on these, so a new view must fill them too.
- `video` (`{id, offset}`) belongs to each song and is written to the library as soon as it changes (`persistVideo`); demos keep theirs, with the fitted tempo, in `ongaku.videos` keyed by demo id.
- Karaoke songs live on disk, not in browser storage: `{id, title, artist, duration, video, notes:[[start, dur, midi]], lyrics:[{t0, t1, s0, s1, wipe:[[t, fraction]], y, text}], analysis, key_confidence}`. Piano video songs: `{title, artist, youtube, bpm, start, notes:[[start, dur, midi, 'R'|'L']]}`, turned into an ABC song by `pianoVideoSong()`.
- Tab notes may carry `slap: 'T'|'P'` (from `T5`/`P7` or a T/P line above the staff).
- Browser storage keys: `ongaku.library`, `ongaku.settings`, `ongaku.current`, `ongaku.videos`, `ongaku.karaokeQueue`, `ongaku.karaokeAdjust` (key shift per song), `ongaku.karaokeScores`. Storage is per origin, so the port must stay 8765 or users lose their saved songs.

## Rules

- Keep it offline-first and dependency-free at runtime. Internet is optional (YouTube, Google Fonts, the jsDelivr fallbacks).
- Wrap every `localStorage` access in try/catch, and keep old saved songs loading after any data-model change (add fields with defaults; never rename existing ones without a migration).
- User-facing text is plain, friendly English, and never uses em dashes.
- Demo songs must be public domain or original.
- Version lives in two places that must match: `VERSION` and the `ongaku-renshuu-version` meta tag in `index.html`. Bump both for any user-visible change.
- Shell scripts use `set -euo pipefail`, must pass `bash -n`, and never need root except for explicit `sudo pacman` steps.

## Checking your work

    tools/check.sh                         # bash -n on the scripts, node --check on the inline JS, version match
    python3 -m http.server 8765 --bind 127.0.0.1    # then open http://127.0.0.1:8765/

If Playwright is available, test in a headless browser: load the page, open the Ode to Joy demos, press play, switch between Tab and Sheet music, and watch for page errors. Check both light and dark color schemes for UI changes.

## Releasing

Commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The GitHub Actions workflow checks the version numbers, builds `ongaku-renshuu.zip`, and attaches it to a release.
