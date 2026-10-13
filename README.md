# Ongaku Renshuu (音楽練習)

A free, MIT-licensed tab player for practicing guitar and bass, with a karaoke mode that turns any karaoke video into a pitch guide and scores your singing. It runs entirely on your computer in your browser.

## Install

From a release zip, unzip it and run the installer. From a clone of this repository:

    git clone https://github.com/baconcraze/ongakurenshuu.git
    cd ongakurenshuu
    ./install.sh

This copies Ongaku Renshuu to ~/.local/share/ongaku-renshuu, adds an `ongaku` command in ~/.local/bin, and puts Ongaku Renshuu in your application menu. It then asks whether to set up Ollama for offline image import. No root needed; it asks for sudo only when installing packages.

Options:

    ./install.sh --with-ollama          also install Ollama (picks ollama-cuda or ollama-rocm for your GPU) and pull a model
    ./install.sh --model qwen2.5vl:32b  choose which vision model to pull
    ./install.sh --no-ollama            skip Ollama
    ./install.sh --yes                  don't ask, answer yes
    ~/.local/share/ongaku-renshuu/install.sh --uninstall

Running the installer again updates an existing install. Your saved songs are kept.

## Update

If you installed from a clone, pull and run the updater from the repository:

    git pull && ./update.sh

From a new download, either run the update script inside the unzipped folder:

    ./update.sh

or, once installed, point the `ongaku-update` command at the new zip:

    ongaku-update ~/Downloads/ongaku-renshuu.zip
    ongaku-update                 # with no argument it uses the newest ongaku-renshuu*.zip in ~/Downloads

It backs up the current version (the last 3 are kept), swaps in the new files, and restarts the server if it was running. Your songs and settings stay put because they live in the browser.

    ongaku-update --rollback      go back to the previous version
    ongaku-update --list          show backups
    ongaku-update --force         reinstall the same version

## Use

    ongaku           start it and open your browser
    ongaku --stop    stop the background server
    ongaku --status
    ongaku --karaoke URL     import a karaoke video (also from the app)
    ongaku --piano URL       make a piano song from a falling-notes video
    ongaku --update-ytdlp    install the newest yt-dlp for video downloads

Ongaku Renshuu runs on http://127.0.0.1:8765. Your library is stored in the browser for that address, so if you change the port with ONGAKU_PORT, you start with an empty library.

## Piano and sheet music

Press **Sheet music** in the bottom bar (or N) to see any guitar or bass tab as standard notation, with the cursor following along. Guitar is written an octave up with the usual "8" under the treble clef, as guitar music normally is.

Piano songs are written in [ABC notation](https://abcnotation.com/), the long-standing standard for writing sheet music as plain text (the piano counterpart of ASCII tab). Library, then **New piano song**, or choose "Piano or melody (ABC notation)" as the Type in the editor. They always show as a grand staff. A short example:

    X:1
    T:Ode to Joy
    M:4/4
    L:1/4
    K:C
    V:RH clef=treble
    V:LH clef=bass
    [V:RH] E E F G | G F E D |
    [V:LH] [C,G,]2 [C,G,]2 | [G,,D,]2 [G,,B,,]2 |

- Header fields: `X:` number, `T:` title, `C:` composer, `M:` time signature, `L:` default note length, `Q:` tempo, `K:` key (`G`, `Am`, `Bb`...). The key's sharps and flats apply automatically.
- `C D E F G A B` is the octave starting at middle C; lowercase `c d e` is the octave above. `'` raises a note an octave and `,` lowers it.
- `^` sharp, `_` flat, `=` natural. A number after a note multiplies its length (`C2`), a slash divides it (`C/`, `C3/2`). `(3` starts a triplet, and `>` / `<` make dotted pairs.
- Chords go in brackets, `[CEG]`; `z` is a rest; `-` ties a note into the next one; `|` ends a bar.
- For piano, name a voice per hand (`V:RH clef=treble`, `V:LH clef=bass`) and start each line with `[V:RH]` or `[V:LH]`. Repeat signs are read but not played twice.

Songs saved in the older simple note text (`RH: E4/4 E4 F4 G4 |`) still load, and switching the Type between the two converts the text for you.

The demos include Ode to Joy, Twinkle Twinkle Little Star, Petzold's Minuet in G, the opening of Für Elise, and the opening of Bach's Prelude in C, all public domain.

When the instrument is a piano (Auto picks one for piano songs, or choose a piano under **Sound**), a piano keyboard appears above the controls. Keys light up as notes play (gold for the right hand, blue for the left), and you can click keys to hear them.

Guitar Pro, MusicXML, and MIDI files with piano or other non-guitar tracks import those tracks too, written out as ABC.

## Sound

Ongaku Renshuu plays tabs with sampled instruments from SoundFont files. Open **Sound** in the bottom bar to pick the SoundFont, the instrument (Auto picks guitar or bass from the tuning), and how much room reverb to add. The classic plucked-string synth is still there if you prefer it.

A small SoundFont (Sonivox, Apache-2.0) is included. For much better guitar and bass, get GeneralUser GS (free, about 32 MB):

    ongaku --get-soundfont

Any .sf2 or .sf3 you drop into ~/.local/share/ongaku-renshuu/soundfonts appears after restarting Ongaku Renshuu (`ongaku --stop`, then `ongaku`). SoundFonts installed system-wide in /usr/share/soundfonts are picked up automatically, for example `sudo pacman -S soundfont-fluid`.

## Play along with a YouTube video

Press **Video** in the bottom bar (or the V key), paste a YouTube link, and press Load video. The video becomes the clock: the tab cursor follows it, and the play button, bar clicks, loops, and the speed slider all control the video. Scrub the video and the tab follows.

To sync a song:

1. Play the video and press "Bar 1 starts now" right on the first downbeat of bar 1 (or type the time).
2. Type a later bar number, pause the video exactly on that bar's downbeat, and press "starts now" again. Ongaku Renshuu fits the tempo so the bars line up.
3. Fine-tune with the nudge buttons while it plays.

Each song keeps its own video and sync: changing songs switches to that song's video, or leaves the player blank if it has none. Library songs and demos remember theirs right away; a new song keeps its video once you save it. To remove a video, clear the link and press Load video.

Tick "Play the tab sound too" to hear Ongaku Renshuu's instrument on top of the recording. This needs an internet connection, and some videos can't be embedded because their owners disallow it. The tab follows a single steady tempo, so live recordings that speed up or slow down will drift a little over long stretches; looping a section and re-syncing that bar works well for practice.

## Library

Search by title, artist or folder, show one instrument (guitar, bass, piano and melody, other), and sort by folder, title, artist or newest. Put a song in a folder with the Folder field in the editor.

## Slap bass

Mark slap and pop notes in bass tab with `T` (thumb slap) and `P` (pop), either right before the fret (`T5`, `P7`) or on a line of letters directly above the staff, lined up with the notes:

      T  T  T P   T P
    G|----------------|
    D|--------9-----7-|
    A|----------------|
    E|0--0--0-----3---|

Marked notes play with the Slap Bass sounds of the SoundFont (General MIDI 36 and 37) and show their letter above the tab and the sheet music. Guitar Pro files keep their slap and pop marks, and image import asks the model to read them too. Try the "Slap groove in E" demo.

## Karaoke

Press **Karaoke** at the top. Three demo songs come with Ongaku Renshuu so you can try it right away: さくらさくら, Amazing Grace, and Ode to Joy with Schiller's German words. All are public domain, with backing tracks made for Ongaku Renshuu, and they work even without the server. To add your own, paste a YouTube link to a karaoke video, to the original song, or both, and press Import, or Import and queue. Whichever is missing is found on YouTube: give only the original song and Ongaku Renshuu looks for its karaoke video (preferring ones that show the lyrics), give only the karaoke video and it looks for the original. It then builds a pitch guide from the original singer's voice (see below) and times the lyric lines from the video's colour wipe. With an NVIDIA graphics card a song is usually ready to sing in about half a minute. Most of that is downloading the video, so a faster connection makes it quicker. The lyric text, which you only need for **Lyrics text** since the video shows the lyrics, is read afterwards by your local Ollama vision model, behind any other songs waiting to be imported, so nothing is sent to an online service and it never slows down the next import. Videos are kept in ~/.local/share/ongaku-renshuu-media.

- **Practice** stays on one song: repeat it, set A and B to repeat a section, or slow it down.
- **Queue** plays like a karaoke machine: songs play one after another with a short countdown, and the next ones are prepared in the background while you sing.
- Turn on **Microphone** to sing along with a live trace of your pitch on the guide: green when you are on pitch, red when you are too high, and blue when you are too low, with an arrow and a hint ("Too high, go down 2 semitones") at the playhead. Your score and how much is on pitch update as you sing, and each note is rated (Perfect, Great, Good, Miss) as it ends. At the end you get a grade, notes hit and your best streak, and your best score is kept for each song and level. Use headphones so the microphone hears you, not the music.
- **Level** sets how strict the scoring is, like the difficulty settings on a karaoke machine. Pitch is measured in cents (100 cents is a semitone), and the start of each note is not judged, so sliding into a note is never penalized:

  | Level | On pitch within | Half credit within | Start of each note left unjudged | Octave |
  |---|---|---|---|---|
  | Easy | a semitone | two semitones | 150 ms | any octave counts |
  | Normal | 50 cents (a quarter tone) | a semitone | 100 ms | any octave counts |
  | Hard | 35 cents | 70 cents | 60 ms | any octave counts |
  | Pro | 25 cents | 50 cents | 30 ms | the guide's own octave (move it with Key) |

  Any octave counting means you can sing an octave up or down from the original singer and still score. Higher levels also need more of each note on pitch for a Perfect.
- Like a karaoke machine, only the guide notes are judged: singing where there is no guide (an intro, a break, an ad-lib) costs nothing. Turn on **Score extras** to have that singing count against your score as well.
- **Voice practice** (at the top of the karaoke screen) has singing exercises that use the microphone: **Find my range** (your lowest and highest comfortable notes, which the other exercises then use), **Hold a note** (long tones, with how steady you were in cents), **Match the note**, a **Five-note scale**, a **Major scale**, an **Arpeggio**, **Intervals** (sing a third, fourth, fifth or octave above a note you hear) and a **Siren** (a smooth slide up and down). A reference note plays first, then you sing to the bar and see your line turn green, red or blue. The karaoke Level sets how close counts, and your best score for each exercise is kept.
- **Key** shifts the guide if it came out in the wrong key, **Mic delay** lines your singing up with the guide, **Guide at top** moves the pitch guide to the bottom of the video when it covers the lyrics, and you can hide or mute the video or show the lyrics as text.

### Where the pitch guide comes from

1. **The original singer's voice (first choice).** A karaoke track has no lead vocal, so Ongaku Renshuu builds the guide from the original recording:
   - **Finding the original.** It searches YouTube using the karaoke video's title, compares the top few results, and keeps the one whose music matches the karaoke track best. Studio versions from the artist's own channel come first. Covers, instrumentals, fan edits (AMV and MAD) and acapellas are skipped, and live performances and collaboration cuts (which can add dialogue or sound effects) are tried only after them. A music video that runs longer than the song, with a story intro or outro, is fine. If it picks the wrong recording, paste the right one under **Original song link** and import again.
   - **Lining it up.** The two recordings are matched by their harmony a few seconds at a time, then to about a hundredth of a second by their beats, so even fast lines sit exactly under the singing.
   - **Following the singer.** Demucs separates the singer's voice and Ongaku Renshuu follows its pitch. Where the bass leaks into the separated voice and the pitch drops far under the sung line, those moments are tracked again above it. Only parts where that voice is clearly present in the original are kept, so instrumental breaks stay empty. Consonants between two sung notes are filled in, and the line between two notes is placed where the voice actually moves from one to the other. Fast runs keep their short notes, each note sits where the voice settles rather than on the scoop it starts with, and lone blips (a breath or a click) are dropped. The thin line over the note bars is the original singer's exact pitch, so you can see slides and vibrato.
   - **How accurate it is.** It was checked against 24 J-pop songs whose isolated vocals are available, imported from real karaoke videos the same way you would, 10 of them only after all tuning was finished. On average the guide sits on vocal silence for about 1.5% of its length, misses about 3% of the clearly sung parts, and is within a semitone of the singer about 97% of the time (23 of the 24 songs above 95%; the lowest, a duet that switches between two singers, at 94%). When it picks the wrong original (a cover or a live version), the guide follows that singer instead, so check the original it names.
   - **Setting up.** The first time, this installs the vocal separator (PyTorch and Demucs, a few GB) into the media folder; run `ongaku --setup-vocals` to do that ahead of time. It is much faster with an NVIDIA graphics card (a few seconds per song) but works on the processor too, and it carries on there by itself when the graphics card is out of memory (for example while Ollama holds a vision model on it). Songs imported with an older version get the new guide when you import them again; the download and the separated voice are reused, so it only takes a moment.
2. **The video's own pitch bars (fallback).** If the original song can't be found or doesn't match, Ongaku Renshuu reads the pitch guide drawn on the video (音程バー) instead. Paged guides (like カラオケ@DIVA and UtaKara) are read by watching each bar change colour as the line passes, which is very accurate; their printed note names, when present, set the key. Scrolling guides are read from how the bars move. Choose **The video's own pitch bars** under Pitch guide from to use only this method.

The song list says which method each song's guide came from. Check a new song against the video the first time.

## Piano songs from falling-notes videos

In the Library, paste a link to a falling-notes piano video (the Synthesia style, with a keyboard at the bottom) and press **Make a piano song**. Ongaku Renshuu watches which keys light up, splits the hands by colour, checks the octave against the audio, works out the tempo, and writes the song as ABC with the video attached. Press **Video** to play along with the recording, and tick **Mute the video** to hear only Ongaku Renshuu.

## Importing songs

Library, then "Import file" reads:

- **Guitar Pro** (.gp, .gpx, .gp5, .gp4, .gp3): tempo, tunings (including capo), and every track.
- **MusicXML** (.musicxml, .xml, .mxl), the standard exchange format that MuseScore, Sibelius, Finale, Dorico and most notation programs export. Tab staves become tab, piano and other parts become ABC.
- **MIDI** (.mid, .midi). Guitar and bass parts become tab with frets chosen for you, piano parts are split into right and left hand at middle C, and timing is rounded to the nearest note value.
- **ABC notation** (.abc) for piano and melody, and plain-text **ASCII tab** (.txt, .tab) for guitar and bass.

Multi-track files keep every pitched track; switch tracks with the menu next to the song title. Drum tracks are skipped, and repeats are written out once, as they appear in the file.

Guitar Pro and MusicXML files are read with alphaTab (MPL-2.0), included in vendor/.

## Image import

Library, then "Import from image". Choose one image or several pages; they are read in the order listed, and you can reorder them with the arrows or by dragging. It reads tab and sheet music: set Instrument to "Piano or melody, sheet music" for piano scores, lead sheets, or any standard notation. Piano grand staves are kept together, each line of music is read whole so the clefs and key signature stay in view, and the result comes out as ABC notation. Choose a reader and a model:

- **Ollama** lists the models you've installed and greys out ones that can't read images. Pull more with `ollama pull <name>` and press Refresh list.
- **Anthropic API** needs a key from console.anthropic.com. The key stays in your browser and requests go straight to Anthropic.

Local models are slower and less accurate than Claude on dense tabs, so check the result before saving.

## Releases

Bump the version in both `VERSION` and the `ongaku-renshuu-version` meta tag in `index.html`, commit, then tag and push:

    git tag v1.5.0 && git push origin v1.5.0

GitHub Actions checks the version numbers and the scripts, builds `ongaku-renshuu.zip`, and attaches it to a new release.

## License

MIT, see LICENSE. Bundled third-party parts keep their own licenses; see THIRD_PARTY_NOTICES.md.
