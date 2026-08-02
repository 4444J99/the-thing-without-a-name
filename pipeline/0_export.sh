#!/usr/bin/env bash
# danse — stage 0: pull the source corpus out of Photos.app.
#
# The album lives at etcetera ▸ ballerina danse ▸ danse (161 camera originals @
# 3264×2448, all shot 2017-06-20, plus the imported frame and 750×750
# transmutations made 2017-07-25). The corpus predicate owns the exact 162-frame
# source denominator after Vision hydration.
#
# Two paths, because the tooling one is the one that breaks:
#   osxphotos  — preferred; stable UUID filenames, pulls iCloud-offloaded originals.
#   AppleScript — fallback; needs no install and no Full Disk Access, because Photos.app
#                 does the reading. This is what actually ran the first time.
#
# Originals never enter git. They land in .work/, which is gitignored.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${DANSE_WORK:-$HERE/.work}"
RAW="$WORK/raw"
ALBUM="${DANSE_ALBUM:-danse}"
FOLDER="${DANSE_FOLDER:-ballerina danse}"
PARENT="${DANSE_PARENT:-etcetera}"

mkdir -p "$RAW"

if [[ "${DANSE_EXPORT:-auto}" != "applescript" ]] && command -v uvx >/dev/null 2>&1; then
  echo "→ osxphotos (via uvx), album '$ALBUM'"
  if uvx osxphotos export "$RAW" \
      --album "$ALBUM" \
      --download-missing \
      --export-by-date=false \
      --skip-edited \
      --filename "{photo.uuid}" 2>&1 | tail -20; then
    echo "exported via osxphotos → $RAW"
    exit 0
  fi
  echo "osxphotos path failed (Full Disk Access?) — falling back to AppleScript" >&2
fi

echo "→ AppleScript export, '$PARENT ▸ $FOLDER ▸ $ALBUM'"
osascript <<EOF
tell application "Photos"
  set sf to folder "$FOLDER" of folder "$PARENT"
  set mi to media items of (album "$ALBUM" of sf)
  export mi to POSIX file "$RAW" with using originals
  return "exported " & (count of mi)
end tell
EOF

echo "exported → $RAW"
find "$RAW" -type f | wc -l | xargs echo "files:"
du -sh "$RAW"
