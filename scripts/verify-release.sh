#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Release verification for Tango. Run from the repo root:
#
#     bash scripts/verify-release.sh                    # prompts for everything
#     bash scripts/verify-release.sh dQw4w9WgXcQ         # prompts for the rest
#     bash scripts/verify-release.sh https://youtu.be/dQw4w9WgXcQ
#
# It asks for the target language and the destination deck rather than taking
# them as positional arguments: checking coverage across languages means
# picking a different pair on nearly every run.
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
ROOT=$(pwd)
PY="$ROOT/.tangovenv/bin/python"
A=$(grep '^ANKI_HOST=' .env | cut -d= -f2)

# JSON built with jq rather than string interpolation, so deck names with
# spaces, quotes or hyphens survive the trip to AnkiConnect intact.
ank() { curl -s "$A" -d "$(jq -nc --arg a "$1" --argjson p "$2" '{action:$a,version:6,params:$p}')"; }
find_in_deck() { ank findNotes "$(jq -nc --arg q "deck:\"$DECK\"" '{query:$q}')"; }
count() { find_in_deck | jq '.result|length'; }

# ── Selection ───────────────────────────────────────────────────────────────
# Prompted rather than positional: testing coverage across languages means
# choosing a different deck and language on nearly every run, and remembering
# argument order for that is friction where it is least wanted.

VID="${1:-}"
while [ -z "$VID" ]; do
  read -rp "YouTube video ID or URL: " VID
done

# A pasted watch URL carries ?v=, &pp=, %3D and slashes. The pipeline itself
# copes -- _normalise_video_id extracts the ID -- but this script also puts
# $VID in a deck name, in an Anki search query and in a make variable, and a
# URL is wrong in all three. Reduce it here, once, using the same rules.
VID=$(PYTHONPATH=src "$PY" -c "
import sys; sys.path.insert(0,'src')
from pipeline.__main__ import _normalise_video_id
try:
    print(_normalise_video_id(sys.argv[1]))
except ValueError as exc:
    print('ERR', exc, file=sys.stderr); sys.exit(1)
" "$VID" 2>/dev/null) || { echo "Could not read a video ID from that input."; exit 1; }
echo "  video id: $VID"

echo
echo "Languages, an offline index means real native definitions:"
for code in $(ls dictionaries/ 2>/dev/null | sed -n 's/wiktionary_\(.*\)\.sqlite/\1/p'); do
  echo "   $code   index built"
done
echo "   en   no index needed, Merriam-Webster covers English"
echo "   (any other supported code works, but definitions will be sparse"
echo "    without an index, build one with: make dictionary LANGUAGE=<code>)"
read -rp "Transcript language code: " LC
[ -z "$LC" ] && { echo "A language is required."; exit 1; }

echo
echo "Definition language: leave blank for native definitions in $LC."
echo "  Set it (e.g. en) to check cross-language mode, which needs a translation"
echo "  model: make translate-model LANGUAGE=$LC DEF_LANG=en"
read -rp "Definition language [native]: " DL
DEF_ARGS=""
if [ -n "$DL" ] && [ "$DL" != "$LC" ]; then
  DEF_ARGS="DEF_LANG=$DL"
fi

echo
echo "Decks in your collection:"
mapfile -t DECKS < <(ank deckNames '{}' | jq -r '.result[]' | sort)
for i in "${!DECKS[@]}"; do printf "  %3d. %s\n" "$((i+1))" "${DECKS[$i]}"; done
echo
echo "Enter a number to use an existing deck, or type a new deck name."
DEFAULT_DECK="Tango_${LC}_${VID}"
read -rp "Deck [$DEFAULT_DECK]: " PICK
if [ -z "$PICK" ]; then
  DECK="$DEFAULT_DECK"
elif [[ "$PICK" =~ ^[0-9]+$ ]] && [ "$PICK" -ge 1 ] && [ "$PICK" -le "${#DECKS[@]}" ]; then
  DECK="${DECKS[$((PICK-1))]}"
else
  DECK="$PICK"
fi
echo
echo "  video      : $VID"
echo "  language   : $LC"
echo "  definitions: ${DL:-native ($LC)}"
echo "  deck       : $DECK"
echo

echo "=== 0. pre-flight ==="
# Anki dedups notes by GUID, and Tango derives the GUID from
# (lemma, video_id, language). So if this video has already been imported
# under this language, a second import UPDATES those notes where they
# already live instead of filling a new deck -- the new deck stays empty and
# every later step reads like a failure. Use a video you have not processed,
# or point DECK at the one that already holds it.
PRIOR=$(ank findNotes "$(jq -nc --arg q "VideoID:$VID" '{query:$q}')" | jq '.result|length')
if [ "$PRIOR" -gt 0 ]; then
  echo "  WARNING: $PRIOR notes for video $VID already exist in this collection."
  echo "  Anki will update those in place rather than populate a new deck."
  echo "  Re-run with a video you have not processed for a clean end-to-end check."
fi
ank createDeck "$(jq -nc --arg d "$DECK" '{deck:$d}')" >/dev/null
echo "  target deck notes before the run: $(count)   (0 is expected, the deck is new)"

echo; echo "=== 1. first run into the empty deck, expect all NEW ==="
# FORCE=1 here too: the video-level "already processed" guard is a separate
# thing from the deck-level duplicate check this script is testing, and it
# would otherwise exit before doing any work on any video you have run before.
RUN1=$(mktemp)
printf 'n\nn\n' | make run VIDEO_ID="$VID" DECK="$DECK" LANGUAGE="$LC" $DEF_ARGS FORCE=1 > "$RUN1" 2>&1
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
"
echo "  notes now: $(count)"

echo; echo "=== 3. SAME video again with FORCE, expect all SKIP, 0 new ==="
printf 'n\nn\n' | make run VIDEO_ID="$VID" DECK="$DECK" LANGUAGE="$LC" $DEF_ARGS FORCE=1 2>&1 \
  | grep -E "Deck check"

echo; echo "=== 4. per-field card quality, read back out of Anki ==="
echo "  (a fallback card carries the literal text 'No definition found'; that"
echo "   counts as missing here, not as a filled field)"
if [ "$(count)" -eq 0 ]; then echo "  no cards in deck, steps 1/2 failed, skipping"; else
IDS=$(find_in_deck | jq -c .result)
ank notesInfo "$(jq -nc --argjson n "$IDS" '{notes:$n}')" | jq -r '
  .result as $n | ($n|length) as $t |
  ["Definition","1st Example Sentence","2nd Example Sentence",
   "Example from Youtube Video","Synonyms","Antonyms"][] as $f |
  ($n | map(select(.fields[$f].value != ""
                   and .fields[$f].value != "No definition found")) | length) as $c |
  "  \($f): \($c)/\($t) (\((100*$c/$t)|floor)%)"'
fi

echo; echo "=== 5. review-mode language (both lines below are EXPECTED) ==="
echo "  a deck whose language resolves:"
printf 'n\n' | make review DECK="$DECK" LANGUAGE="$LC" 2>&1 | grep -E "Target language" | sed 's/^/    /'
echo "  a deck whose name carries no language, falling back to en is the"
echo "  designed behaviour here, not a failure:"
printf 'n\n' | make review DECK="My Words" 2>&1 | grep -E "Could not infer" | sed 's/^/    /' 

echo; echo "=== 6. paths anchor to the project, not the shell's cwd ==="
cd /tmp && PYTHONPATH="$ROOT/src" "$PY" -c "
from pipeline import config as c
import os
print('  DB_PATH :', c.DB_PATH, '| exists:', c.DB_PATH.exists())
print('  DICT_DIR:', c.DICT_DIR, '| exists:', c.DICT_DIR.exists())
print('  stray pipeline.db in /tmp:', os.path.exists('/tmp/pipeline.db'))" 2>&1 | grep -v "UserWarning"
cd "$ROOT"

echo; echo "=== 7. make install no longer dies on a missing bin/pip ==="
make -n install 2>/dev/null | grep -m1 "pip install"

echo; echo "=== 8. suite ==="
make test 2>&1 | tail -2
