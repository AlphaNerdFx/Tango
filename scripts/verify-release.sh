#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Release verification for Tango. Run from the repo root:
#
#     bash scripts/verify-release.sh <VIDEO_ID> <LANGUAGE>
#     bash scripts/verify-release.sh dQw4w9WgXcQ en
#
# This exists because a written checklist goes stale silently, which has
# happened repeatedly in this repo, while a script goes stale loudly by
# failing. It is not a test suite -- `make test` is. It checks the things
# unit tests structurally cannot: that a real run against real Anki produces
# the cards it claims to.
#
# REQUIREMENTS: a running Anki with AnkiConnect, network access, jq, curl,
# an ANKI_HOST line in .env, and for non-English a built dictionary index
# (`make dictionary LANGUAGE=<code>`).
#
# IT WRITES TO YOUR REAL COLLECTION. It creates a deck named
# Tango_V045_<VIDEO_ID> and imports cards into it. Nothing is deleted.
#
# USE A VIDEO YOU HAVE NOT PROCESSED BEFORE. Anki dedups notes by GUID and
# Tango derives the GUID from (lemma, video_id, language), so re-importing a
# video you already have updates those notes where they already live rather
# than filling the new deck -- every later step then reads like a failure.
# Step 0 warns when this applies.
# ---------------------------------------------------------------------------
set -u
VID="${1:?usage: bash scripts/verify-release.sh <VIDEO_ID> <LANGUAGE>}"
LC="${2:?usage: bash scripts/verify-release.sh <VIDEO_ID> <LANGUAGE>}"
DECK="Tango_V045_${VID}"
ROOT=$(pwd)
PY="$ROOT/.tangovenv/bin/python"
A=$(grep '^ANKI_HOST=' .env | cut -d= -f2)
ank() { curl -s "$A" -d "{\"action\":\"$1\",\"version\":6,\"params\":$2}"; }
count() { ank findNotes "{\"query\":\"deck:$DECK\"}" | jq '.result|length'; }

echo "=== 0. pre-flight ==="
# Anki dedups notes by GUID, and Tango derives the GUID from
# (lemma, video_id, language). So if this video has already been imported
# under this language, a second import UPDATES those notes where they
# already live instead of filling a new deck -- the new deck stays empty and
# every later step reads like a failure. Use a video you have not processed,
# or point DECK at the one that already holds it.
PRIOR=$(ank findNotes "{\"query\":\"VideoID:$VID\"}" | jq '.result|length')
if [ "$PRIOR" -gt 0 ]; then
  echo "  WARNING: $PRIOR notes for video $VID already exist in this collection."
  echo "  Anki will update those in place rather than populate a new deck."
  echo "  Re-run with a video you have not processed for a clean end-to-end check."
fi
ank createDeck "{\"deck\":\"$DECK\"}" >/dev/null; echo "  target deck notes: $(count)"

echo; echo "=== 1. first run into the empty deck — expect all NEW ==="
# FORCE=1 here too: the video-level "already processed" guard is a separate
# thing from the deck-level duplicate check this script is testing, and it
# would otherwise exit before doing any work on any video you have run before.
RUN1=$(mktemp)
printf 'n\nn\n' | make run VIDEO_ID="$VID" DECK="$DECK" LANGUAGE="$LC" FORCE=1 > "$RUN1" 2>&1
grep -E "Target language|Deck check|Definitions:|Cards:|Package:" "$RUN1"

echo; echo "=== 2. import the .apkg ==="
# Import the package THIS run produced, read from its own output, not the
# newest file matching the video id -- an older .apkg names an older deck
# inside itself and would import there instead.
APKG=$(grep -oE '/[^ ]+\.apkg' "$RUN1" | tail -1)
echo "  package: ${APKG:-NONE PRODUCED}"
PYTHONPATH=src "$PY" -c "
import sys; sys.path.insert(0,'src')
from pipeline import deck
from pipeline.__main__ import _translate_wsl_path
print('  importPackage ->', deck._anki_request('importPackage', path=_translate_wsl_path('$APKG')))
" 2>/dev/null
echo "  notes now: $(count)"

echo; echo "=== 3. SAME video again with FORCE — expect all SKIP, 0 new ==="
printf 'n\nn\n' | make run VIDEO_ID="$VID" DECK="$DECK" LANGUAGE="$LC" FORCE=1 2>&1 \
  | grep -E "Deck check"

echo; echo "=== 4. per-field card quality, read back out of Anki ==="
if [ "$(count)" -eq 0 ]; then echo "  no cards in deck — steps 1/2 failed, skipping"; else
IDS=$(ank findNotes "{\"query\":\"deck:$DECK\"}" | jq -c .result)
ank notesInfo "{\"notes\":$IDS}" | jq -r '
  .result as $n | ($n|length) as $t |
  ["Definition","1st Example Sentence","2nd Example Sentence",
   "Example from Youtube Video","Synonyms","Antonyms"][] as $f |
  ($n | map(select(.fields[$f].value != "")) | length) as $c |
  "  \($f): \($c)/\($t) (\((100*$c/$t)|floor)%)"'
fi

echo; echo "=== 5. review language — was silently English for every deck ==="
printf 'n\n' | make review DECK="$DECK" LANGUAGE="$LC" 2>&1 | grep -E "Target language"
printf 'n\n' | make review DECK="My Words" 2>&1 | grep -E "Could not infer"

echo; echo "=== 6. paths anchor to the project, not the shell's cwd ==="
cd /tmp && PYTHONPATH="$ROOT/src" "$PY" -c "
from pipeline import config as c
import os
print('  DB_PATH :', c.DB_PATH, '| exists:', c.DB_PATH.exists())
print('  DICT_DIR:', c.DICT_DIR, '| exists:', c.DICT_DIR.exists())
print('  stray pipeline.db in /tmp:', os.path.exists('/tmp/pipeline.db'))" 2>/dev/null
cd "$ROOT"

echo; echo "=== 7. make install no longer dies on a missing bin/pip ==="
make -n install 2>/dev/null | grep -m1 "pip install"

echo; echo "=== 8. suite ==="
make test 2>&1 | tail -2
