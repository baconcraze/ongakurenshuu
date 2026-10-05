#!/usr/bin/env bash
# Update an installed Ongaku Renshuu from a new download, keeping your settings and songs.
#
#   ongaku-update                   use the newest ongaku-renshuu*.zip in ~/Downloads
#   ongaku-update path/to/ongaku-renshuu.zip
#   ongaku-update path/to/unzipped/ongaku-renshuu
#   ongaku-update --force ...       reinstall even if the version is the same
#   ongaku-update --rollback        go back to the version before the last update
#   ongaku-update --list            show saved backups
#
# Your saved songs live in your browser, so updating never touches them.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/ongaku-renshuu"
BACKUPS="$APP_DIR/backups"
BIN_DIR="$HOME/.local/bin"
KEEP=3
FILES=(index.html ongaku-renshuu.sh install.sh update.sh README.md VERSION)

say()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m  !!\033[0m %s\n' "$*" >&2; }
die()  { warn "$*"; exit 1; }
ver()  { [[ -f $1/VERSION ]] && tr -d '[:space:]' < "$1/VERSION" || echo unknown; }

FORCE=0; MODE=update; SRC=""
while (( $# )); do
  case "$1" in
    --force) FORCE=1 ;;
    --rollback) MODE=rollback ;;
    --list) MODE=list ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "Unknown option: $1 (try --help)" ;;
    *) SRC="$1" ;;
  esac
  shift
done

[[ $EUID -ne 0 ]] || die "Run this as your normal user, not root."
[[ -f $APP_DIR/index.html ]] || die "Ongaku Renshuu isn't installed in $APP_DIR. Run install.sh from the download first."

server_running() { [[ -x $APP_DIR/ongaku-renshuu.sh ]] && "$APP_DIR/ongaku-renshuu.sh" --status 2>/dev/null | grep -q '^Running'; }
stop_server()    { [[ -x $APP_DIR/ongaku-renshuu.sh ]] && "$APP_DIR/ongaku-renshuu.sh" --stop >/dev/null 2>&1 || true; }
start_server()   { "$APP_DIR/ongaku-renshuu.sh" --serve >/dev/null 2>&1 && ok "Restarted the Ongaku Renshuu server" || warn "Could not restart the server; run: ongaku"; }
relink() {
  mkdir -p "$BIN_DIR"
  ln -sfn "$APP_DIR/ongaku-renshuu.sh" "$BIN_DIR/ongaku"
  [[ -f $APP_DIR/update.sh ]] && ln -sfn "$APP_DIR/update.sh" "$BIN_DIR/ongaku-update"
  chmod +x "$APP_DIR"/*.sh 2>/dev/null || true
}
copy_release() { # $1 = source dir
  local from=$1 f
  for f in "${FILES[@]}"; do [[ -f $from/$f ]] && install -m 644 "$from/$f" "$APP_DIR/$f"; done
  if [[ -d $from/vendor ]]; then rm -rf "$APP_DIR/vendor"; cp -r "$from/vendor" "$APP_DIR/vendor"; fi
  if [[ -d $from/soundfonts ]]; then  # adds bundled fonts, never removes ones you added
    mkdir -p "$APP_DIR/soundfonts"
    cp -f "$from"/soundfonts/*.sf2 "$from"/soundfonts/*.txt "$APP_DIR/soundfonts/" 2>/dev/null || true
  fi
  relink
}

# ---------- List / rollback ----------
if [[ $MODE == list ]]; then
  if compgen -G "$BACKUPS/*" >/dev/null; then ls -1t "$BACKUPS"; else echo "No backups yet."; fi
  exit 0
fi
if [[ $MODE == rollback ]]; then
  last="$(ls -1dt "$BACKUPS"/*/ 2>/dev/null | head -n1 || true)"
  [[ -n $last ]] || die "No backup to roll back to."
  say "Rolling back to $(basename "$last")"
  was=0; server_running && was=1
  stop_server
  copy_release "${last%/}"
  [[ -d ${last%/}/vendor ]] || rm -rf "$APP_DIR/vendor"
  rm -rf "$last"
  ok "Now on version $(ver "$APP_DIR")"
  (( was )) && start_server
  echo "    Reload Ongaku Renshuu in your browser to use it."
  exit 0
fi

# ---------- Find the new release ----------
if [[ -z $SRC ]]; then
  here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
  if [[ $here != "$APP_DIR" && -f $here/index.html ]]; then
    SRC="$here"
  else
    SRC="$(ls -1t "$HOME"/Downloads/ongaku-renshuu*.zip 2>/dev/null | head -n1 || true)"
    [[ -n $SRC ]] || die "No update found. Pass the new ongaku-renshuu.zip or its folder: ongaku-update ~/Downloads/ongaku-renshuu.zip"
  fi
fi
[[ -e $SRC ]] || die "Not found: $SRC"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if [[ -f $SRC ]]; then
  say "Unpacking $SRC"
  python3 -m zipfile -e "$SRC" "$TMP" || die "That doesn't look like a valid zip file."
  NEW="$(dirname "$(find "$TMP" -maxdepth 3 -name index.html -print -quit)")"
  [[ $NEW != . ]] || die "No index.html inside $SRC."
else
  NEW="$(cd "$SRC" && pwd)"
fi
[[ -f $NEW/index.html && -f $NEW/ongaku-renshuu.sh ]] || die "$SRC doesn't contain a Ongaku Renshuu release (index.html and ongaku-renshuu.sh)."

OLD_V="$(ver "$APP_DIR")"; NEW_V="$(ver "$NEW")"
say "Installed: $OLD_V    New: $NEW_V"
if [[ $OLD_V == "$NEW_V" && $OLD_V != unknown && $FORCE -eq 0 ]]; then
  ok "Already up to date. Use --force to reinstall anyway."
  exit 0
fi

# ---------- Back up, replace, restart ----------
was=0; server_running && was=1
stop_server

stamp="$(date +%Y%m%d-%H%M%S)-v$OLD_V"
mkdir -p "$BACKUPS/$stamp"
for f in "${FILES[@]}"; do [[ -f $APP_DIR/$f ]] && cp "$APP_DIR/$f" "$BACKUPS/$stamp/"; done
[[ -d $APP_DIR/vendor ]] && cp -r "$APP_DIR/vendor" "$BACKUPS/$stamp/"
ok "Backed up the current version to backups/$stamp"
ls -1dt "$BACKUPS"/*/ 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -rf

copy_release "$NEW"
ok "Updated to version $NEW_V"
(( was )) && start_server

echo
say "Done. Reload Ongaku Renshuu in your browser (Ctrl+Shift+R) to use the new version."
echo "    Changed your mind? Run: ongaku-update --rollback"
if [[ ! -f $APP_DIR/soundfonts/GeneralUser-GS.sf2 ]]; then
  echo "    New in this version: sampled instruments. For much better sound, run: ongaku --get-soundfont"
fi
