#!/usr/bin/env bash
# Ongaku Renshuu launcher: serves Ongaku Renshuu on 127.0.0.1 in the background and opens it.
# Usage: ongaku [--serve | --stop | --status | --get-soundfont | --help]
set -euo pipefail
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PORT="${ONGAKU_PORT:-8765}"   # keep this fixed: your library is saved per address
URL="http://127.0.0.1:${PORT}/index.html"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/ongaku-renshuu-${PORT}.pid"

SF_DIR="$DIR/soundfonts"
GUGS_URL="https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/GeneralUser-GS.sf2"
GUGS_LIC="https://raw.githubusercontent.com/mrbumpy409/GeneralUser-GS/main/documentation/LICENSE.txt"

up() { curl -fs -o /dev/null "$URL"; }

# List the SoundFonts Ongaku Renshuu can use (its own folder plus system ones in /usr/share/soundfonts).
index_soundfonts() {
  mkdir -p "$SF_DIR"
  local f
  for f in /usr/share/soundfonts/*.sf2 /usr/share/soundfonts/*.sf3; do
    [[ -e $f ]] || continue
    [[ $(basename "$f") == default.sf2 ]] && continue
    [[ -e $SF_DIR/$(basename "$f") ]] || ln -s "$f" "$SF_DIR/$(basename "$f")" 2>/dev/null || true
  done
  find "$SF_DIR" -maxdepth 1 -xtype l -delete 2>/dev/null || true
  python3 - "$SF_DIR" <<'PY' || true
import json, os, sys
d = sys.argv[1]; out = []
for f in sorted(os.listdir(d), key=str.lower):
    if f.lower().endswith(('.sf2', '.sf3')):
        try: out.append({"file": f, "size": os.path.getsize(os.path.join(d, f))})
        except OSError: pass
with open(os.path.join(d, 'index.json'), 'w') as fh: json.dump(out, fh)
PY
}

get_soundfont() {
  mkdir -p "$SF_DIR"
  if [[ -f $SF_DIR/GeneralUser-GS.sf2 ]]; then echo "GeneralUser GS is already installed."; return 0; fi
  echo "Downloading GeneralUser GS (about 32 MB) by S. Christian Collins..."
  curl -fL --progress-bar -o "$SF_DIR/GeneralUser-GS.sf2.part" "$GUGS_URL" || { rm -f "$SF_DIR/GeneralUser-GS.sf2.part"; echo "Download failed." >&2; return 1; }
  mv "$SF_DIR/GeneralUser-GS.sf2.part" "$SF_DIR/GeneralUser-GS.sf2"
  curl -fsL -o "$SF_DIR/GeneralUser-GS-LICENSE.txt" "$GUGS_LIC" || true
  index_soundfonts
  echo "Installed. Reload Ongaku Renshuu and pick it under Sound."
}

case "${1:-}" in
  --stop)
    if [[ -f $PIDFILE ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then echo "Ongaku Renshuu stopped."; else echo "Ongaku Renshuu is not running."; fi
    rm -f "$PIDFILE"; exit 0 ;;
  --status)
    if up; then echo "Running at $URL"; else echo "Not running."; fi; exit 0 ;;
  --get-soundfont) get_soundfont; exit $? ;;
  -h|--help)
    echo "Usage: ongaku [--serve | --stop | --status]"
    echo "  --serve          start the server without opening the browser"
    echo "  --get-soundfont  download the GeneralUser GS instrument sounds (about 32 MB)"
    echo "Starts Ongaku Renshuu at $URL and opens it in your browser."
    echo "Set ONGAKU_PORT to use a different port (your library is saved per port)."
    exit 0 ;;
  ""|--serve) ;;
  *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
esac

index_soundfonts
if ! up; then
  command -v python3 >/dev/null || { echo "Ongaku Renshuu needs python3." >&2; exit 1; }
  setsid python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$DIR" >/dev/null 2>&1 </dev/null &
  echo $! > "$PIDFILE"
  for _ in $(seq 30); do up && break; sleep 0.1; done
  if ! up; then
    echo "Could not start Ongaku Renshuu on port $PORT. Another program may be using it; try ONGAKU_PORT=8766 ongaku" >&2
    exit 1
  fi
fi

if [[ ${1:-} == --serve ]]; then
  echo "Ongaku Renshuu is serving at $URL"; exit 0
fi
if command -v xdg-open >/dev/null; then
  (xdg-open "$URL" >/dev/null 2>&1 &)
else
  echo "Open $URL in your browser."
fi
echo "Ongaku Renshuu is running at $URL"
echo "Stop it with: ongaku --stop"
