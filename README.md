# Ongaku Renshuu (音楽練習)

A free, MIT-licensed tab player for practicing guitar and bass. It runs entirely on your computer in your browser.

## Install

From a release zip, unzip it and run the installer. From a clone of this repository:

    git clone https://github.com/YOUR-USERNAME/ongaku-renshuu.git
    cd ongaku-renshuu
    ./install.sh

This copies Ongaku Renshuu to ~/.local/share/ongaku-renshuu, adds an `ongaku` command in ~/.local/bin, and puts Ongaku Renshuu in your application menu. It then asks whether to set up Ollama for offline image import. No root needed; it asks for sudo only when installing packages.

Options:

    ./install.sh --with-ollama          also install Ollama (picks ollama-cuda or ollama-rocm for your GPU) and pull a model
    ./install.sh --model qwen2.5vl:32b  choose which vision model to pull
    ./install.sh --no-ollama            skip Ollama
    ./install.sh --yes                  don't ask, answer yes
    ~/.local/share/ongaku-renshuu/install.sh --uninstall

Running the installer again updates an existing install. Your saved songs are kept.

## Coming from Fretline

Ongaku Renshuu used to be called Fretline. Running `./install.sh` moves an existing Fretline install over: your saved songs, settings, and downloaded SoundFonts carry across, and the old `fretline` commands and menu entry are removed.

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

Ongaku Renshuu runs on http://127.0.0.1:8765. Your library is stored in the browser for that address, so if you change the port with ONGAKU_PORT, you start with an empty library.

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
3. Fine-tune with the nudge buttons while it plays, then **Save to library** so the link and sync are kept with the song.

Tick "Play the tab sound too" to hear Ongaku Renshuu's instrument on top of the recording. This needs an internet connection, and some videos can't be embedded because their owners disallow it. The tab follows a single steady tempo, so live recordings that speed up or slow down will drift a little over long stretches; looping a section and re-syncing that bar works well for practice.

## Importing songs

Library, then "Import file" reads Guitar Pro files (.gp, .gpx, .gp5, .gp4, .gp3) and plain-text ASCII tab. Guitar Pro files keep their tempo, tunings (including capo), and every guitar and bass track; switch tracks with the menu next to the song title. Drum tracks are skipped, and repeats are written out once, as they appear in the file.

Guitar Pro files are read with alphaTab (MPL-2.0), included in vendor/.

## Image import

Library, then "Import from image". Choose a reader and a model:

- **Ollama** lists the models you've installed and greys out ones that can't read images. Pull more with `ollama pull <name>` and press Refresh list.
- **Anthropic API** needs a key from console.anthropic.com. The key stays in your browser and requests go straight to Anthropic.

Local models are slower and less accurate than Claude on dense tabs, so check the result before saving.

## Releases

Bump the version in both `VERSION` and the `ongaku-renshuu-version` meta tag in `index.html`, commit, then tag and push:

    git tag v1.5.0 && git push origin v1.5.0

GitHub Actions checks the version numbers and the scripts, builds `ongaku-renshuu.zip`, and attaches it to a new release.

## License

MIT, see LICENSE. Bundled third-party parts keep their own licenses; see THIRD_PARTY_NOTICES.md.
