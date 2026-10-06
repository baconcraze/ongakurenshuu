#!/usr/bin/env bash
# Quick checks for Ongaku Renshuu. Run from anywhere: tools/check.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
fail=0

for f in install.sh update.sh ongaku-renshuu.sh tools/check.sh; do
  if bash -n "$f"; then echo "ok   bash -n $f"; else echo "FAIL bash -n $f"; fail=1; fi
done

if command -v node >/dev/null; then
  tmp="$(mktemp --suffix=.js)"; trap 'rm -f "$tmp"' EXIT
  python3 - "$tmp" <<'PY'
import re, sys
html = open('index.html', encoding='utf-8').read()
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
open(sys.argv[1], 'w', encoding='utf-8').write('\n;\n'.join(scripts))
PY
  if node --check "$tmp"; then echo "ok   node --check (inline JavaScript)"; else echo "FAIL node --check (inline JavaScript)"; fail=1; fi
else
  echo "skip node --check (node is not installed)"
fi

for f in tools/*.py; do
  if python3 -m py_compile "$f" 2>/dev/null; then echo "ok   python $f"; else echo "FAIL python $f"; fail=1; fi
done
find tools -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

v_file="$(tr -d '[:space:]' < VERSION)"
v_meta="$(grep -o 'name="ongaku-renshuu-version" content="[^"]*"' index.html | sed 's/.*content="//; s/"$//')"
if [[ $v_file == "$v_meta" ]]; then echo "ok   version $v_file"; else echo "FAIL VERSION is $v_file but index.html says $v_meta"; fail=1; fi

for f in vendor/alphaTab.min.js vendor/vexflow-bravura.js soundfonts/sonivox.sf2 karaoke-demos/index.json; do
  if [[ -s $f ]]; then echo "ok   $f"; else echo "FAIL missing $f"; fail=1; fi
done

exit $fail
