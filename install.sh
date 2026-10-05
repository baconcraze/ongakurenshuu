#!/usr/bin/env bash
# Ongaku Renshuu installer for Linux (tuned for Arch; works on other distros too).
#
#   ./install.sh                 install, and ask about Ollama for offline image import
#   ./install.sh --with-ollama   also install Ollama and pull a vision model
#   ./install.sh --no-ollama     skip Ollama entirely
#   ./install.sh --model NAME    vision model to pull (default qwen2.5vl:7b)
#   ./install.sh --yes           answer yes to every question
#   ./install.sh --uninstall     remove Ongaku Renshuu (your saved songs in the browser are kept)
set -euo pipefail

SRC="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/ongaku-renshuu"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="$DATA/applications/ongaku-renshuu.desktop"
ICON_FILE="$DATA/icons/hicolor/scalable/apps/ongaku-renshuu.svg"
MODEL="qwen2.5vl:7b"
OLLAMA_MODE=ask
ASSUME_YES=0
UNINSTALL=0

say()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m  !!\033[0m %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
ask()  {
  (( ASSUME_YES )) && return 0
  [[ -t 0 ]] || return 1
  local a; read -rp "    $1 [y/N] " a; [[ ${a,,} == y* ]]
}

while (( $# )); do
  case "$1" in
    --with-ollama) OLLAMA_MODE=yes ;;
    --no-ollama)   OLLAMA_MODE=no ;;
    --model)       shift; [[ $# -gt 0 ]] || die "--model needs a name"; MODEL="$1" ;;
    -y|--yes)      ASSUME_YES=1 ;;
    --uninstall)   UNINSTALL=1 ;;
    -h|--help)     sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
  shift
done

[[ $EUID -ne 0 ]] || die "Run this as your normal user, not root. It asks for sudo only when installing packages."

refresh_menus() {
  command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
  command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$DATA/icons/hicolor" >/dev/null 2>&1 || true
}

# ---------- Uninstall ----------
if (( UNINSTALL )); then
  say "Removing Ongaku Renshuu"
  [[ -x $APP_DIR/ongaku-renshuu.sh ]] && "$APP_DIR/ongaku-renshuu.sh" --stop >/dev/null 2>&1 || true
  rm -rf "$APP_DIR"
  [[ -L $BIN_DIR/ongaku ]] && rm -f "$BIN_DIR/ongaku"
  [[ -L $BIN_DIR/ongaku-update ]] && rm -f "$BIN_DIR/ongaku-update"
  rm -f "$DESKTOP_FILE" "$ICON_FILE"
  refresh_menus
  ok "Ongaku Renshuu removed."
  echo "    Songs you saved live in your browser and were left alone. Ollama and its models were not touched."
  exit 0
fi

# ---------- Checks ----------
for f in index.html ongaku-renshuu.sh install.sh update.sh; do
  [[ -f $SRC/$f ]] || die "Missing $f. Run install.sh from inside the unzipped ongaku-renshuu folder."
done

say "Checking requirements"
missing=()
command -v python3 >/dev/null || missing+=(python)
command -v curl    >/dev/null || missing+=(curl)
command -v xdg-open >/dev/null || missing+=(xdg-utils)
if (( ${#missing[@]} )); then
  if command -v pacman >/dev/null; then
    say "Installing ${missing[*]} with pacman"
    sudo pacman -S --needed --noconfirm "${missing[@]}"
  else
    die "Please install: ${missing[*]} (python3, curl, xdg-utils), then run this again."
  fi
fi
ok "python3, curl and xdg-open found"

# Video imports (karaoke and falling-notes piano videos) also need ffmpeg, numpy and Pillow.
vmissing=()
command -v ffmpeg >/dev/null || vmissing+=(ffmpeg)
python3 -c 'import numpy' 2>/dev/null || vmissing+=(python-numpy)
python3 -c 'import PIL' 2>/dev/null || vmissing+=(python-pillow)
if (( ${#vmissing[@]} )); then
  if command -v pacman >/dev/null && ask "Video imports need ${vmissing[*]}. Install them with pacman now?"; then
    sudo pacman -S --needed --noconfirm "${vmissing[@]}" || warn "Could not install ${vmissing[*]}; video imports will not work until they are installed."
  else
    warn "Video imports need: ${vmissing[*]}. Everything else works without them."
  fi
else
  ok "ffmpeg, numpy and Pillow found (for video imports)"
fi

# ---------- Install files ----------
say "Installing Ongaku Renshuu to $APP_DIR"
[[ -x $APP_DIR/ongaku-renshuu.sh ]] && "$APP_DIR/ongaku-renshuu.sh" --stop >/dev/null 2>&1 || true
mkdir -p "$APP_DIR" "$BIN_DIR" "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")"
install -m 644 "$SRC/index.html" "$APP_DIR/index.html"
install -m 755 "$SRC/ongaku-renshuu.sh"   "$APP_DIR/ongaku-renshuu.sh"
install -m 755 "$SRC/install.sh" "$APP_DIR/install.sh"
install -m 755 "$SRC/update.sh"  "$APP_DIR/update.sh"
[[ -f $SRC/VERSION ]] && install -m 644 "$SRC/VERSION" "$APP_DIR/VERSION"
if [[ -d $SRC/vendor ]]; then rm -rf "$APP_DIR/vendor"; cp -r "$SRC/vendor" "$APP_DIR/vendor"; fi
if [[ -d $SRC/tools ]]; then rm -rf "$APP_DIR/tools"; cp -r "$SRC/tools" "$APP_DIR/tools"; fi
if [[ -d $SRC/soundfonts ]]; then mkdir -p "$APP_DIR/soundfonts"; cp -f "$SRC"/soundfonts/*.sf2 "$SRC"/soundfonts/*.txt "$APP_DIR/soundfonts/" 2>/dev/null || true; fi
[[ -f $SRC/README.md ]] && install -m 644 "$SRC/README.md" "$APP_DIR/README.md"
ln -sfn "$APP_DIR/ongaku-renshuu.sh" "$BIN_DIR/ongaku"
ln -sfn "$APP_DIR/update.sh" "$BIN_DIR/ongaku-update"
ok "Files installed; commands ongaku and ongaku-update linked in $BIN_DIR"


cat > "$ICON_FILE" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#3a2018"/>
  <g stroke="#a3adb8" stroke-width="1.6">
    <line x1="10" y1="20" x2="54" y2="20"/><line x1="10" y1="28" x2="54" y2="28"/>
    <line x1="10" y1="36" x2="54" y2="36"/><line x1="10" y1="44" x2="54" y2="44"/>
  </g>
  <rect x="35" y="12" width="4.5" height="40" rx="2" fill="#c9a227"/>
  <g font-family="sans-serif" font-weight="700" font-size="10" fill="#f0d27a" text-anchor="middle">
    <text x="20" y="31.5">3</text><text x="27" y="23.5">5</text><text x="47" y="39.5">7</text>
  </g>
</svg>
SVG

cat > "$DESKTOP_FILE" <<DESK
[Desktop Entry]
Type=Application
Name=Ongaku Renshuu
Name[ja]=音楽練習
GenericName=Tab Player
Comment=Play, loop and practice guitar and bass tabs
Exec=$APP_DIR/ongaku-renshuu.sh
Icon=ongaku-renshuu
Terminal=false
Categories=AudioVideo;Audio;Music;Education;
Keywords=guitar;bass;tab;tablature;practice;ongaku;renshuu;
DESK
refresh_menus
ok "Added Ongaku Renshuu to your application menu"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not in your PATH, so the 'ongaku' command won't be found in a terminal."
     echo "    Add this to your shell config: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# ---------- Optional: better instrument sounds ----------
if [[ ! -f $APP_DIR/soundfonts/GeneralUser-GS.sf2 ]]; then
  say "Optional: better instrument sounds"
  echo "    GeneralUser GS is a free 32 MB SoundFont with much nicer guitar and bass samples than the small one included."
  if ask "Download it now?"; then "$APP_DIR/ongaku-renshuu.sh" --get-soundfont || warn "Download failed. Try later with: ongaku --get-soundfont"
  else echo "    Later, run: ongaku --get-soundfont"; fi
fi

# ---------- Optional: Ollama for offline image import ----------
ollama_up() { curl -fs -o /dev/null http://127.0.0.1:11434/api/tags; }

install_ollama() {
  if command -v pacman >/dev/null; then
    local pkg=ollama
    if command -v lspci >/dev/null; then
      local gpus; gpus="$(lspci | grep -Ei 'vga|3d|display' || true)"
      if grep -qi nvidia <<<"$gpus"; then pkg=ollama-cuda
      elif grep -Eqi 'amd|ati' <<<"$gpus"; then pkg=ollama-rocm
      fi
    fi
    say "Installing $pkg with pacman"
    sudo pacman -S --needed --noconfirm "$pkg"
  else
    say "Your distro doesn't use pacman; Ollama's official installer can set it up."
    ask "Run Ollama's installer from ollama.com now?" || { warn "Skipped. See https://ollama.com/download"; return 1; }
    curl -fsSL https://ollama.com/install.sh | sh
  fi
}

want_ollama=0
case "$OLLAMA_MODE" in
  yes) want_ollama=1 ;;
  no)  ;;
  ask)
    say "Optional: offline image import"
    echo "    Ongaku Renshuu can turn tab screenshots into playable tab using a local vision model through Ollama."
    echo "    That's a large download (Ollama plus a model of about 6 GB). You can also use an Anthropic API key instead."
    ask "Set up Ollama now?" && want_ollama=1 ;;
esac

if (( want_ollama )); then
  if command -v ollama >/dev/null; then ok "Ollama is already installed"; else install_ollama || want_ollama=0; fi
fi

if (( want_ollama )) && command -v ollama >/dev/null; then
  if ! ollama_up; then
    if systemctl list-unit-files ollama.service >/dev/null 2>&1; then
      say "Starting the Ollama service"
      sudo systemctl enable --now ollama
    else
      warn "No ollama service found. Start it yourself with: ollama serve"
    fi
    for _ in $(seq 50); do ollama_up && break; sleep 0.2; done
  fi
  if ollama_up; then
    ok "Ollama is running"
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
      ok "$MODEL is already downloaded"
    elif ask "Download the vision model $MODEL now? (several GB)"; then
      ollama pull "$MODEL" && ok "$MODEL is ready"
    else
      echo "    Later, run: ollama pull $MODEL"
    fi
  else
    warn "Ollama didn't start. Check: systemctl status ollama"
  fi
fi

echo
say "Done. Start Ongaku Renshuu from your app menu, or run: ongaku"
echo "    Stop the background server with: ongaku --stop"
echo "    Update later with: ongaku-update path/to/new/ongaku-renshuu.zip"
echo "    Remove it with: $APP_DIR/install.sh --uninstall"
