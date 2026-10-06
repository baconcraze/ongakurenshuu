# Ongaku Renshuu (音楽練習)

A free, MIT-licensed tab player for practicing guitar and bass. It runs entirely on your computer in your browser.

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

Press **Karaoke** at the top. Three demo songs come with Ongaku Renshuu so you can try it right away: さくらさくら, Amazing Grace, and Ode to Joy with Schiller's German words. All are public domain, with backing tracks made for Ongaku Renshuu, and they work even without the server. To add your own, paste a YouTube link to a karaoke video with a pitch guide (音程バー) and press Import, or Import and queue. Ongaku Renshuu downloads the video, reads the pitch bars into notes (scrolling and paged layouts both work), finds the key from the audio, times the lyric lines from their colour wipe, and reads the lyric text with your local Ollama vision model, so nothing is sent to an online service. A song can be sung about a minute after you import it; the lyric text follows in the background. Videos are kept in ~/.local/share/ongaku-renshuu-media.

- **Practice** stays on one song: repeat it, set A and B to repeat a section, or slow it down.
- **Queue** plays like a karaoke machine: songs play one after another with a short countdown, and the next ones are prepared in the background while you sing.
- Turn on **Microphone** to see your pitch on the guide and get a score at the end: a grade, how much was on pitch, notes hit and your best streak. Your best score is kept for each song. Use headphones so the microphone hears you, not the music.
- **Key** shifts the guide if it came out in the wrong key, **Mic delay** lines your singing up with the guide, and you can hide or mute the video or show the lyrics as text.

The pitch guide is read from the picture. Paged guides (like カラオケ@DIVA and UtaKara) are read by watching each bar change colour as the line passes, which is very accurate; their printed note names, when present, set the key. Scrolling guides are read from how the bars move. Check a new song against the video the first time.

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
