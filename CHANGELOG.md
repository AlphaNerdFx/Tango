# Changelog

All notable changes to Tango are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While at `0.x`, a MINOR bump means new capability **or anything requiring a
migration**; see `CLAUDE.md` §15 and `ROADMAP.md` §1 for the rule that picks
the number, and `ROADMAP.md` §2 for the goal attached to each planned tag.

Entries for v0.4.5 and earlier were reconstructed from tag messages and
`git log` when this file was created at v0.5.0. They summarise each release
rather than list every change.

## [Unreleased]

Working towards v0.12.0, runs on modest hardware. Everything below is on
`main` and unreleased: the published 0.11.0 has none of it.

Most of this came out of a code and documentation audit run on 8 and 9
September 2026. The full account, with the numbers each conclusion rests on,
is in `docs/history/CODE_AUDIT_2026-09.md`. The decisions it produced are
ADR-012, ADR-013 and ADR-014.

### Added

- **Exit code 3: a package was written, but not one card got a definition.**
  Found by installing the published package into a clean virtual environment
  and running it as a new user. dictionaryapi.dev answered `HTTP 522` after
  19.6 seconds against an 8 second timeout, the circuit breaker tripped, and
  a real English run produced 97 cards with a definition on none of them and
  **exited 0**. Nothing scriptable could tell that deck from a good one. The
  condition is "not one definition", not a percentage: zero is unambiguous
  and a threshold invites false alarms. A run where every word was already in
  the deck is not a failure and still exits 0. ARCHITECTURE 8.53, and the
  codes are now frozen in ROADMAP section 3.
- **The Docker image is published**, as `yousseflarbi/tango`, tags `latest`
  and `0.11.0`. It installs the wheel from PyPI on purpose, so building it
  tests the artefact users actually install. AnkiConnect is not reachable
  from inside a container and the image says so when a run needs it.
- **`make audit`**, running `bandit` and `pip-audit` together. Both are in
  the `dev` extra.
- **`tango doctor` reports a setting whose value is not a number**, naming
  the setting, the value written, and the fact that the default was used
  instead. It already reported settings that nothing reads.
- **`tango doctor` reports a publishing credential left in `.env`.** A token
  is not a setting, and `load_dotenv()` puts everything in that file into
  the environment of every run.
- `scripts/measure_footprint.py`, which measures the v0.12.0 memory targets
  per stage in separate interpreters. None of those targets had ever been
  measured.

### Changed

- **The local LibreTranslate server is its own extra**,
  `tango-anki[translation-server]`. Translation itself never needed it:
  community mirrors and argostranslate cover the feature, while the server
  pulled **134 MB across 36 packages**, including PyMuPDF at 65 MB and lxml,
  for document conversion this project does not do. `make translate-setup`
  installs both, so the guided path is unchanged. ADR-014.
- **`.env.example` now ships the code defaults.** It had `MW_RATE_LIMIT=2`
  and `MW_BURST=5` against code defaults of 4.0 and 8, so copying the
  documented template halved the throughput the project had deliberately
  settled on, and nothing said so.
- **`LIBRETRANSLATE_URL` is removed.** It was documented in `.env.example`
  and in a code comment as "set this to your own instance and it is used
  first", and the constant it filled was read and never used, from the day
  it was added on 10 July 2026. A local server has always gone in
  `LIBRETRANSLATE_MIRRORS`, which is consumed, so the setting was removed
  rather than wired up. Anyone who still has the key set is now told by
  `tango doctor` that it has no effect. ADR-012.
- The transcript search is **8.5x faster on a hit and 13.3x on a miss**, and
  a whole package build 28% faster. It was 66% of the build. ARCHITECTURE
  8.51.
- `typing.Callable` moved to `collections.abc` in four modules, where it has
  lived since Python 3.9.
- Security floors raised for `requests` and `nltk`. Dependency advisories
  went from 37 to 11, and all eleven that remain are inside the translation
  server extra, which no normal install pulls.

### Fixed

- **Colour no longer leaks into pipes and log files.** The seven ANSI
  constants were unconditional, so `tango run > log.txt` wrote
  `^[[31m^[[1m[err ]^[[0m` into the file. It is now decided per stream at
  import: `NO_COLOR` disables it when merely present, `FORCE_COLOR` enables
  it for CI, and otherwise it follows `isatty()`. stdout and stderr are
  decided separately, so a redirected run still colours the error you see on
  screen. The progress reporter had checked `isatty()` since it was written;
  the five output helpers and 39 other call sites never had.
- **`tango doctor` no longer calls a working machine broken.** One counter
  covered both blocking and optional gaps, so it exited 1 whenever any
  optional index was absent while printing "Each is optional -- the pipeline
  runs without them". Both halves were wrong at once: without a spaCy model
  nothing runs at all, and a missing Japanese dictionary is fine. It now
  exits non-zero only when something stops a run, and says "Ready to run"
  otherwise. `tango doctor && tango run` works on a normal machine again.
- **`doctor` never suggested the English offline index.** The loop that
  reports missing indexes carried `code != "en"`, residue from the decision
  ARCHITECTURE 8.19 took and ADR-011 reversed. `_DISCOURAGED` was emptied
  when ADR-011 landed and this was not, so the one language whose only
  safety net is the index was the one language never told to build it. It is
  now reported as "web only, so an outage means no definitions".
- **A dead source was told only to "retry later".** That fixes nothing and
  says the same thing tomorrow. The advice now also names
  `tango build-dictionary <language>`, which is the durable answer.
- **A blank setting no longer stops the program.** `KEY=` in a `.env` loads
  as an empty string rather than leaving the name unset, so eighteen numeric
  settings raised `ValueError` during `import config`, which happens before
  `main()` exists to turn it into a message, and nine text settings silently
  lost their defaults. A blank value now means unset everywhere, as it
  already did for paths. `.env.example` ships ten keys with blank values, so
  this was reachable by following the documented setup. ADR-012,
  ARCHITECTURE 8.52.
- **Thirteen unit tests were making real network requests**, which breaks
  CLAUDE.md 3.5. Twelve reached en.wiktionary.org or dictionaryapi.dev and
  one reached Wikidata. None was written that way: each mocked every source
  its function had at the time and stopped being complete when another was
  added, silently. `conftest.py` now fails any default-run test that opens
  an outbound connection. ADR-013, ARCHITECTURE 8.49.
- **A schema migration could swallow a locked or corrupt database.** Two
  `except Exception: pass` blocks around `ALTER TABLE` made an unwritable
  database look exactly like a column that already existed.
- **A mutable default argument** in `transcript.fetch_transcript`, and six
  re-raises that dropped the original traceback for want of `from exc`.
- **An unvalidated URL scheme** before `urlretrieve`. The URL is always
  https because it is built from a constant template, but `urlretrieve`
  opens `file://` and `ftp://` too, so the invariant is now enforced rather
  than reconstructed by the reader.
- A 35 MB measurement intermediate was living in `dictionaries/`, whose size
  is one of v0.12.0's acceptance targets.
- Eight live settings had never reached `.env.example`, among them
  `IMAGES_ENABLED`, the headline setting of the release published that
  morning.

### Removed

- `translation.check_packages_dir()`, 44 lines. Unreachable:
  `_repair_packages_dir()` runs at import and pops the environment variable,
  so the diagnostic could never find the problem it was written to report.
- Six dead declarations across five modules, sixteen unused test imports,
  and `translation.LIBRETRANSLATE_LOCAL`.

### Documentation

A full audit on 9 September 2026, checking every claim against the thing it
describes: code comments against code, markdown against code, `.env.example`
against `config.py`, and the repository against what PyPI and Docker Hub
actually serve.

- **`ARCHITECTURE.md` section 10 was wrong in every figure.** It claimed 734
  unit tests across eleven files at 88% over 1963 statements. Measured: 1307
  tests across 16 files, 89% over 3654. Eleven of its fifteen per-module
  numbers were stale, and three modules recorded at 100% were not. This is
  the fourth stale coverage figure this repository has carried.
- **`TASKS.md` listed three shipped things as open**: the Typer migration
  (v0.7.0), the Dockerfile (v0.8.2, published 8 September), and phase 3
  images, whose entry read "deliberately not built" after v0.11.0 shipped
  it. The Dockerfile entry also named a `python:3.11-slim` base against the
  real `python:3.10-slim`.
- **Two dangling cross-references.** `tests/conftest.py` and `ARCHITECTURE.md`
  both cited section 18.7 as an ARCHITECTURE section. It is a CLAUDE.md
  section and ARCHITECTURE has no section 18. Every other `ARCHITECTURE n.n`
  citation in the repository was checked and resolves.
- **The README advertised an extra the published package does not have.**
  `pip install "tango-anki[translation,translation-server]"` fails on 0.11.0,
  verified against the PyPI JSON API. The README now says so and names the
  release it arrives in.
- **Docker Hub had no description**, short or full. One is now written,
  kept in the repository, and pushed.
- **The GitHub wiki contradicted the code on nine pages and was rewritten.**
  It documented two settings that do not exist (`API_DELAY`, removed when
  definition fetching became concurrent, and `LIBRETRANSLATE_URL`, removed
  in this release), two flags as if they were settings (`LANGUAGE`,
  `DEF_LANG`), a `SPACY_MODEL` setting that was never real, and
  `python -m pipeline --list-languages`, which is both the pre-console-script
  invocation and a flag v0.7.0 deleted. It called `MW_API_KEY` required when
  it is optional, listed ten card fields when there are fourteen, gave
  Python 3.9 as the floor when it is 3.10, said 40 languages when 24 have
  models, quoted PyTorch at 1.5 GB when the CPU build is 725 MB, and offered
  no pip or Docker install path at all despite the package being on PyPI
  since v0.8.0.

  Two pages gave actively harmful advice. **The WSL page told users to find
  their Windows gateway IP and hardcode it into `ANKI_HOST`**, which turns
  off the automatic fallback that has handled this since v0.10.0 and pins an
  address that goes stale on reboot, and it said auto-import could not work
  under WSL when the path translation that makes it work is already there.
  **The troubleshooting page recommended Webshare first**, whose free tier
  was measured making extraction worse, and told users to delete
  `pipeline.db` for a schema complaint, which throws away a definition cache
  that is expensive to rebuild.
- **CLAUDE.md 3.2's card field table listed indices 0 to 11**, five weeks
  after `Image` and `Attribution` were appended for v0.11.0, and the
  paragraph under it said a new field is index 12 while index 12 was taken.
  Prose elsewhere in the same file had it right. That table is what someone
  reads before adding a field, so it is now pinned to `cards.FIELDS` by a
  test.
- Stale test and coverage figures corrected in `CLAUDE.md`, `SESSION.md` and
  `HANDOVER.md`, and the "`make check` takes about ten minutes" claim that
  every handover carried replaced with the measured 145 seconds.
- New: ADR-012, ADR-013, ADR-014, ARCHITECTURE 8.49 to 8.52, and
  `docs/history/CODE_AUDIT_2026-09.md`.

Checks that came back clean are recorded too, in the audit document: version
agreement across six places, every documented `make` target existing, and
the PyPI badge pinning confirmed working on the published 0.11.0 page rather
than only in `make dist`.

**Five of these checks are now tests**, so the audit does not have to be
repeated by hand and the same drift cannot recur silently. `make check`
fails if a document cites an ARCHITECTURE section that does not exist, if it
tells a reader to run a `make` target the Makefile lacks, if CLAUDE.md,
CHANGELOG and the Dockerfile disagree about the current release, if
`__version__` falls behind the CHANGELOG, or if a setting is read into a
constant that nothing uses. A sixth, added alongside them, fails if
`.env.example` ships a value that disagrees with the code default, which is
the check that would have caught the MW pacing years earlier than a person
did.

## [0.11.0] - 2026-09-08

Images on cards, gated to concrete nouns. ADR-009 phase 3. Off by default and
asked for per run with `--images`, because a picture roughly doubles a deck
that already carries audio.

**Migration.** `Image` and `Attribution` are appended as fields 12 and 13.
`tango run` aligns the notetype before importing, so an existing collection
gains two empty fields and keeps every note, its content and its scheduling;
verified against a live 5,311-note collection, which merged without forking.
Anki will ask for a full sync afterwards, which is what a schema change
always costs.

### Added

- **`Image` and `Attribution` card fields**, appended at indices 12 and 13
  under the rule in CLAUDE.md 3.2. Verified against a live 4,773-note
  collection before shipping: no notetype fork, and 0 existing field values
  changed. Anki will ask for a full sync, which is expected for a schema
  change.
- **`src/pipeline/images.py`**, which resolves a lemma to a Wikidata concept
  and gates on its `instance of` claims. The judgement attaches to the
  concept rather than the word, so `Hund` and `chien` both reach Q144 and one
  answer serves every language.
- **`--images` and `--no-images` on `run`, `review` and `backlog`.** Off by
  default, so trying pictures is one flag rather than an edit to `.env`.
  Three states on purpose: `--images` and `--no-images` each override the
  environment for one run, and neither flag leaves `IMAGES_ENABLED` to
  decide, so an install that turns them on can still build one deck without.
- **`IMAGES_ENABLED`**, default false, now the per-install default that the
  flags override. A run with images off makes no network calls for them.
- **`tango doctor` reports card images**, including that the gate covers
  every language rather than only the 19 in OMW.
- **`scripts/measure_image_sources.py`**, which measures coverage against
  the definition cache rather than a hand-picked word list.

### Changed

- **The image gate reads `subclass of` when an item has no `instance of`.**
  A common noun is a class in Wikidata and a class is described by what it is
  a subclass of, so asking only for `instance of` refused 127 of 785 nouns,
  more than the denylist and missing files together. Coverage of the
  definition cache goes from 33.1% to **45.9%**: French 33.4 to 43.9, German
  36.3 to 54.3, English 21.6 to 33.8. Some abstract words now get an
  illustrative picture, which is the named cost of the extra hundred.
- **A picture that shows another sense than the card's definition is
  dropped**, before it is downloaded. French `palais` was printing a
  photograph of a monumental building beside "paroi supérieure qui sépare la
  fosse nasale de la bouche". Measured: 4 of 214 imaged French words, 0 of
  121 German and 0 of 25 English, each drop read by hand.

  The comparison runs only where the card is showing a row from the offline
  index. Judging a Merriam-Webster definition against Wiktionary rows dropped
  29 correct pictures of 159 on a real English run, because the two word a
  sense differently and that is not a disagreement about meaning.
- **The card is one screen tall and the image takes the height the text
  leaves**, between a sixth and a half of the screen, instead of a fixed 30%
  of the viewport that could not know how much room the text had used. The
  credit line now sits with the picture rather than at the bottom of the
  screen.
- **Images are requested as 480px thumbnails, not originals.** The first real
  download was 9.2 MB for one photograph shown on the card at 240px; the same
  file is 46 KB as a thumbnail.
- **Resolution is batched at 50 items per request**, about 24 requests for a
  deck instead of 1600, verified as 16 of 16 identical results against the
  one-at-a-time path.
- **The README no longer advertises languages that cannot produce a card.**
  It said "40 languages ... including Arabic"; there are 45 codes, 25 of them
  usable, and spaCy has no Arabic model, so an Arabic deck failed at run time.

### Fixed

- **Abstract concepts that reached a photograph.** `leben` returned a
  newborn, `cowardice` the Cowardly Lion, `government` a group portrait of
  Dutch ministers and `loi` the Palais-Bourbon. Twelve Wikidata classes and
  disambiguation pages are now refused by name.
- **Credit lines were empty on the batched path**, because Commons reports
  titles with spaces while the filenames come from a URL with underscores.
  An image shipped without its credit breaches the licence rather than
  merely looking untidy.
- **A lead image shipped with no credit at all.** The Wikipedia route hands
  back a thumbnail URL, so the credit lookup asked Commons about
  `500px-Scout_Girl.jpg` rather than `Scout_Girl.jpg`, found no page, and
  returned nothing, which is indistinguishable from a file that genuinely
  has no credit. Found by counting credits in a real deck: 4 of 15 pictures
  had none and all four came from that route, two of them CC BY.
- **A Commons credit could put live markup on a card.** `Artist` metadata is
  wiki text anyone can edit and a card is HTML in a webview, but tags were
  stripped before entities were decoded, so `&lt;img src=x onerror=...&gt;`
  passed the stripper untouched and became a working tag. Now decoded,
  stripped, then escaped. ARCHITECTURE 8.48.
- A multi-line Commons credit no longer breaks the card layout.


## [0.10.0] - 2026-09-05

Runs on any operating system, and now there is evidence for the claim.

### Added

- **CI on Linux, macOS and Windows.** The rung's own words were "because
  should work is not evidence", and until this job existed every claim about
  the other two platforms was an assertion: development happens on WSL2.
  Linux covers the Python range, macOS and Windows run one version each,
  because what they test is the operating system rather than the
  interpreter. Five jobs, not nine.

  It found a real bug on its first run.

### Fixed

- **An interrupted index build left a partial file behind on Windows.**
  `wiktdata.py` and `antonyms.py` both deleted the `.building` file in their
  error handler while the SQLite connection was still open. POSIX unlinks an
  open file happily and frees the inode when the last handle closes; Windows
  refuses with `WinError 32` and the partial file survived for the next run
  to inherit. Both now close before unlinking.

- **A WSL user who clones to `~` could not import.** Anki runs on the
  Windows side and can only open paths Windows can see. `/mnt/<drive>` paths
  are translated; anything under `/home` or `/tmp` cannot be, because it has
  no Windows equivalent. What the user saw was AnkiConnect's own
  file-not-found, which says nothing about why a file that plainly exists
  cannot be found. It now names the cause and both ways out.

  Gated on which host answered rather than on being under WSL, because a WSL
  user can run Anki inside WSL through WSLg, and there localhost is right
  and POSIX paths are exactly what AnkiConnect wants. Blocking those would
  have broken a setup that works.

- **Four tests had only ever run on Linux.** Three compared paths against
  POSIX literals, when `str(Path)` uses the native separator, and one
  patched `Path.resolve` to a `/mnt/c` path that becomes a drive-relative
  `WindowsPath` the WSL regex cannot match. They were testing the platform
  rather than the code.

### Changed

- **The CLI is the primary path and the Makefile is a convenience.** GNU
  make is not a reasonable requirement on Windows, and `make check-os`
  correctly refuses to run there without Git Bash, WSL or Cygwin. Every
  user-facing target already had a `tango` equivalent, so the change is to
  the documentation: CLAUDE.md section 6 now leads with the command a user
  actually has.

## [0.9.0] - 2026-09-05

Nothing fails without saying why.

Every release before this one fixed failures as they were met: seven
messages in v0.7.0, eight stale flag names in v0.8.0, a port 8765 traceback
in v0.8.2. Each was found by someone tripping over it or reading the code.
CLAUDE.md 4.4 has always required that no expected failure produces a
traceback, and it was enforced by noticing. This release went looking
instead, and found seven things.

### Added

- **`TangoError`, one base for every failure raised on purpose.** The
  project defines twenty typed exceptions across nine modules and they had
  nothing in common, so the entry point could not tell a failure someone
  wrote a message for from a genuine bug. Every module keeps its own types
  and its own wording; this only adds a shared ancestor, so every existing
  `except SomeError` behaves exactly as before.

  The rule for raising one: if a message can tell the user what to do, it is
  a `TangoError`. If the only honest message is "this should not have
  happened", it is not.

- **An unexpected error is a message, not a traceback.** CLAUDE.md 4.4 has
  always said the pipeline must not produce a traceback for an expected
  failure, and it was enforced by noticing rather than by checking. Anything
  unanticipated printed a Python traceback at the user. It now prints what
  went wrong, says it is a bug rather than something they did, and links the
  issue tracker. `TANGO_DEBUG=1` restores the traceback for anyone debugging.

- **A typed error when the run database cannot be opened.** Reproduced
  rather than imagined: a truncated file gives `sqlite3.DatabaseError: file
  is not a database`, and an unwritable path gives
  `sqlite3.OperationalError: unable to open database file`. Both reached the
  user raw. Each now names the cause and the fix, and neither is reported as
  a bug in Tango, because neither is one.

- **A test that scans for the mistake rather than listing the cases.** Any
  exception added later without the base fails the suite. It found dead code
  while it was being written.

### Added

- **The .apkg write is guarded.** The last step in the pipeline was the one
  place a bare `OSError` could reach the user, and the worst: the transcript
  is fetched, every definition is looked up and paid for, the audio is
  downloaded, and then a full disk takes the run with it. Both the directory
  creation and the write are typed now, and the message says how many cards
  were built and not saved.

- **`tango doctor` names settings in `.env` that nothing reads.** A setting
  that does nothing is a failure with no message, which is this release's
  whole subject. Found in a real `.env`: `SPACY_MODEL=en_core_web_sm`, which
  looks exactly like it chooses the model and is read by nothing, and
  `API_DELAY`, a leftover. The model comes from the language code; the knob
  that exists is `SPACY_MODEL_SIZE_OVERRIDE`.

  `config.KNOWN_ENV_KEYS` declares the real names, and a test walks every
  `os.getenv` in the package and fails if the list drifts either way. It
  found two keys missing from the list within seconds of being written, and
  a third that was invisible because it was read through a constant rather
  than a literal.

- **A `MANIFEST.in`, so the Dockerfile ships in the sdist.** It stands
  alone: it installs from PyPI and needs no other file from the repository,
  so someone who downloads the source distribution can build the image from
  it. v0.8.2 shipped without it because setuptools only ships what it is
  told about.

### Changed

- **`src/` holds the importable package and nothing else.** It also held
  `src/images/`, nine icons for a UI that does not exist, never imported and
  never packaged. Checked against the PyPA guidance and six well-known
  projects: `src/` is "the code that is intended to be importable",
  `psf/requests` has exactly `src/requests/`, and none of the six keeps a
  loose image at its repository root. The icons and two loose root diagrams
  are now in `docs/assets/`.

- **`SECURITY.md` no longer names a version.** It said "v0.4.x (latest)"
  while the project was on v0.8.2. It now points at PyPI and the releases
  page, which cannot go stale.

### Fixed

- **Two implementations of WSL detection**, both reading `/proc/version`,
  introduced the same day `deck.py` needed the answer `__main__` already
  had. Collapsed to one.

- **A dead exception class.** `_TranslationTimeoutError` was defined and
  documented and never raised or caught anywhere.


## [0.8.2] - 2026-09-04

An install that looks after itself. Three items cut from v0.8.0 so the name
could be claimed, plus one gap a reader found in the code.

### Added

- **A missing spaCy model is offered, not just reported.** A fresh
  `pip install tango-anki` got as far as fetching the transcript and then
  stopped with "spaCy model not found. Run: ...". The message was correct and
  the timing was not: the work was already spent, and the user was sent away
  to run a second command before seeing anything work.

  The check now runs before the transcript is fetched, where it costs
  nothing, and on a terminal it offers to do the download. Answering yes
  installs the model and the run carries straight on. Answering no, or
  running with the output piped, prints `tango install-model <lang>` and
  exits 1 rather than prompting into something that cannot answer.

- **`tango uninstall`.** `pip uninstall tango-anki` removes about 130 KB of
  Python and leaves everything that takes space. Measured on the development
  machine: 1.1 GB of dictionary indexes, 82 MB of generated packages, 46 MB
  of cached audio and a 4.9 MB definition cache. That is reasonable, since
  pip only owns what it installed, and it is still a surprise. Nothing else
  knew those files existed, so nothing else could offer to remove them.

  It lists every location with its size and what losing it would cost, then
  asks. `--dry-run` reports and stops. `--yes` skips the prompt for scripts.
  Run with the output piped and no `--yes`, it reports and deletes nothing,
  because the alternative is a script silently destroying a definition cache
  that took hours of API calls to build. Paths come from `config`, so a user
  who redirected `DICT_DIR` or `DB_PATH` gets their own locations rather than
  the defaults.

- **A Dockerfile, for the "just run it" case.** No Python setup, no
  virtualenv, no model download:

  ```bash
  docker build -t tango .
  docker run --rm -v "$PWD/out:/data/output" tango run <id> --deck "French"
  ```

  The English model is baked in, so the image works with no setup. Other
  languages are not, because 24 models would multiply a 663 MB image for
  something most users never ask for; install one into the mounted volume
  with `tango install-model fr`. All state lives in `/data`, which is a
  volume, so the cache survives and the .apkg reaches the host. It runs as a
  non-root user, since a container writing to a mounted volume as root
  leaves files the host user cannot delete.

  Built and run before release: 663 MB, `tango --version` answers,
  `en_core_web_sm` loads, `/data` is writable by uid 1000, and a file written
  inside reaches the host owned by the host user.

### Fixed

- **Something else on port 8765 produced a traceback.** AnkiConnect answers
  on 8765 and nothing guarantees AnkiConnect is what is there. A different
  service on that port, or an `ANKI_HOST` pointing somewhere stale, got as
  far as a successful HTTP request and then died on `response.json()` with
  "Expecting value: line 1 column 1 (char 0)". Against CLAUDE.md 4.4, and
  useless to the reader.

  Three shapes are now typed errors that name the address, the port and
  `ANKI_HOST`: an answer that is not JSON, an HTTP error status, and JSON
  with no `result` field. Deliberately not `AnkiNotRunningError`, because
  the port answered, so "start Anki" is the one piece of advice already
  ruled out.

  Found by a reader looking at the code, not by a failure, which is why
  nothing covered it.

## [0.8.1] - 2026-09-03

Documentation, for the people who can now install this.

### Fixed

- **The README told pip users to run `make`.** Every command in it assumed a
  cloned repository: `make run VIDEO_ID=... DECK=...`, `make dictionary`,
  `make antonyms`, `make translate-setup`. None of that exists after
  `pip install tango-anki`, and the README is also the PyPI description, so
  it was the first thing a new user read and none of it worked for them.
  Every command is now a `tango` subcommand, with the Makefile equivalents
  kept in a collapsed section for people working in a clone.

  PyPI freezes a release's description at upload time and will not let it be
  edited, so correcting the published page needs a release of its own. That
  is what this one is.

- **The version badge was stuck at v0.5.3**, five releases behind, because it
  was hardcoded. It now reads live from PyPI and from the GitHub releases
  API, alongside a new badge for the package itself, so neither can go stale
  again.

- **The roadmap table in the README was wrong about the plan**, not just the
  version. It still showed the ordering from before 27 August 2026, when
  packaging sat at v0.10.0 behind cross-platform support. It now shows the
  real sequence and where the project actually is.

- **A `.pyc` was committed.** `.gitignore` listed `tests/__pycache__` and
  `src/pipeline/__pycache__` by name, so `scripts/__pycache__` was never
  covered. Replaced with the directory-anchored `__pycache__/` and
  `*.py[cod]`, which catch every directory including ones that do not exist
  yet.

- **Em dashes removed** from the documents and code comments that had them,
  per the project's own writing style rule in CLAUDE.md 17.

### Added

- **`.env.example` documents the PyPI publishing keys.** `PYPI_USER` must be
  the literal string `__token__`, not a PyPI account name; an account name
  paired with a `pypi-` token is rejected with a 403 that does not say why.
  That is what tripped this project's first upload, and it is worth writing
  down rather than rediscovering.

## [0.8.0] - 2026-09-03

Packaged and installable, and **published to PyPI as `tango-anki`**. The
funnel finally has a top: `pip install tango-anki` is something a stranger
can type, where before the only route in was cloning the repository.

Three planned items were cut to v0.8.1 rather than delay claiming the name,
since an unclaimed distribution name can be taken by anyone: first-run model
downloads, a Dockerfile, and an uninstall that removes the indexes. None of
them stops anyone installing or running the tool.

### Added

- **A distribution name: `tango-anki`.** `tango` on PyPI is an unrelated
  project, so the name pip fetches and the name you type cannot be the same
  string. They do not have to be: the console script stays `tango`, which is
  the one anybody types twice. `pyproject.toml` said `yt-anki-pipeline` until
  now, which described the mechanism rather than the product and matched
  nothing a user ever sees. Also fills in the metadata a published package
  needs and had none of: authors, keywords, classifiers, project URLs and an
  SPDX licence.

- **`tango --version`.** The first question about any bug report is which
  build produced it, and until v0.7.0 there was no installed command to ask.
  Reads `pipeline.__version__`, the single source of truth, rather than
  installed metadata, which under an editable install reports whatever the
  version was when it was installed.

### Changed

- **WSL reaches Anki without being told where it is.** `ANKI_HOST` defaults
  to `http://localhost:8765`, which is right on macOS, native Linux, native
  Windows and WSL2 with mirrored networking. On WSL2's default NAT network
  it is wrong: Anki runs on the Windows side and localhost is the Linux VM.
  The documented fix was to find the gateway with `ip route` and paste it
  into `.env`, which worked until Windows rebooted and reassigned it.

  A refused connection is now retried once against the Windows host read
  from `/proc/net/route`, and the address that answered is kept for the rest
  of the run. Deliberately a retry and not a different default: defaulting
  to the gateway under WSL would break mirrored networking, where localhost
  is correct. An explicit `ANKI_HOST` is never second-guessed, a timeout is
  never retried, and when both addresses fail the message names both and
  points at AnkiConnect's `127.0.0.1` bind, which is the real cause most of
  the time. ARCHITECTURE 8.45.

- **`nltk` is an extra rather than a base dependency.** It supplements the
  synonym and antonym fields and nothing else, and an install without it is
  a working install with thinner synonyms, verified by blocking the import
  and watching both entry points return empty lists instead of raising.
  `pip install tango-anki[wordnet]` for it. The base install is 334 MB
  measured in a clean venv, of which 236 MB is spacy and the numeric stack
  it needs, so this is 13 MB and a corpus download rather than a
  breakthrough. It is the only saving available while spacy is the NLP
  engine.

### Fixed

- **Two documented facts that were never true.** ROADMAP and CLAUDE.md both
  said `ANKI_HOST` defaults to a WSL gateway IP. It defaults to localhost
  and always has; the gateway IP was in one developer's uncommitted `.env`.
  ROADMAP also asked for a default install with "no translation, no torch",
  which it already had. Both corrected by measuring. Left visible rather
  than quietly rewritten, for the same reason the antonym cause was in
  v0.6.0.

- **Eight messages told users to run flags that v0.7.0 deleted.** The
  migration to subcommands left the old spellings behind in shipped output:
  a missing translation model said `python -m pipeline
  --install-translation`, an unmapped language said `--list-languages`, a
  missing spaCy model said `--install-model`, an AnkiConnect error said
  `--doctor`, and a stale index said `--build-dictionary` or
  `--build-antonyms`. None of those flags exist. This is a worse failure
  than a stale document, because naming the fix was v0.7.0's own goal and
  these named a fix that could only fail.

  One of them had a test, which passed throughout because it asserted the
  string `--doctor` rather than the intent, so the test and the bug went
  stale as a matched pair. The replacement asserts the command that exists
  and that `python -m pipeline` is absent, and a second test scans every
  module for the deleted spellings so the next one is caught by the suite
  rather than by a reader.

  The module docstring in `__main__.py` also still documented the removed
  flag surface in full.

## [0.7.0] - 2026-09-03

The command line as a product: the first release aimed at someone who did not
write it. Nothing here changes a card or asks anything of an existing
collection. It changes what you type, what the run tells you while it works,
and what a failure says when it stops.

### Added

- **A real command, with subcommands.** `tango run <id> --deck "..."`,
  plus `review`, `backlog`, `languages`, `doctor`, `setup`, `install-model`,
  `install-translation`, `build-dictionary` and `build-antonyms`. The old
  surface put every mode behind a boolean flag on one parser, so `--help`
  listed sixteen options without indicating that `--review` and `--video-id`
  are different programs, and nothing stopped you passing both.

  There is also a console entry point for the first time: the project had no
  `[project.scripts]` at all, so even an editable install gave you
  `python -m pipeline` rather than a verb. **This is a breaking CLI change**,
  made deliberately now, while the interface has no installed users, rather
  than after v0.8.0 publishes it.

- **A long run says what it is doing, and how long it took.** Pacing
  Merriam-Webster (8.43) turned the definition phase into minutes of silence
  on a large English video, and silence is indistinguishable from a hang.
  Every phase now reports its own elapsed time, and the definition phase
  carries a progress line with a completion estimate.

  The line redraws in place on a terminal, so a thousand-word run stays one
  line rather than a thousand. Redirected anywhere else it prints one line
  per decile instead: carriage returns in a log file are noise, and
  `tango run ... > run.log` is a normal thing to do here. The estimate is
  elapsed divided by completed, which is honest because the work per word is
  uniform, and a cleverer estimate that is wrong is worse than a simple one
  that is roughly right.

- **English can have an offline index, and should.** `make dictionary
  LANGUAGE=en` used to warn you off. That advice was measured on 7 August,
  a week before IPA and Pronunciation became card fields, and it has been
  reversed. On a real 1094-lemma English deck the index supplies IPA for
  96.4% of words, audio for 97.0% and an example for 90.3%, all offline,
  where before English carried none of those whenever dictionaryapi.dev was
  unreachable.

  Merriam-Webster is still tried first for definitions, because they are
  better written. What changed is that it is no longer the only thing
  holding English up: its free tier allows 1000 queries a day per key and
  one 1094-word video exceeds that on its own, and its licence does not
  cover a commercial product. 502 MB to download, 236 MB on disk, the
  smallest of the four indexes. ADR-011.

### Fixed

- **Merriam-Webster is paced, and a source that stops is reported.** A real
  1094-word English run shipped 167 cards with a definition and 927 without,
  and said "Done". MW answered the first 167 and then nothing: five workers
  were pushing about 18 requests a second, five consecutive failures tripped
  the circuit breaker, and a tripped breaker skips its source for the rest of
  the run while nothing in the summary says so.

  MW now goes through the same leaky bucket `media.py` has used since v0.5.2,
  at a deliberately conservative 4 requests a second (`MW_RATE_LIMIT`), and
  the run summary names any source the breaker gave up on, says that it
  explains the missing content, and gives the two ways out. ARCHITECTURE 8.43.

- **One word no longer becomes two cards.** A 1079-card French deck carried
  `voyez` beside `voir` and `soit` beside `être`: 15 cards, 1.4%, each an
  inflected form of another card in the same deck, because spaCy returned the
  surface form as the lemma. The offline index already records the base form,
  so an inflected form is now folded onto it, keeping its count and its
  surface forms. Only when the base form is in the same run, so a word met
  only in an inflected form is still taught as the learner met it.
  ARCHITECTURE 8.44.

- **An index miss no longer costs English its pronunciation.**
  `_resolve_pronunciation` consults the index first for every language and
  returned early on a miss, which was free while English had no index. With
  one, a word the index does not carry would have gone from "sometimes has
  audio" to "never has audio". It falls through to dictionaryapi.dev now.

- **`make doctor` no longer reports `Error 1`.** The report's own last line
  says every missing item is optional, and make printed a failure directly
  underneath it. The CLI keeps its exit code, which setup scripts branch on;
  the make target no longer propagates it.

- **Failures name the fix.** Seven messages stopped the run while stating
  only the outcome, leaving the user to find the flag in `--help` or guess
  that one exists. Now: an already-processed video names `--force` and shows
  the exact command; a missing translation model names
  `--install-translation de:en` and the alternative of dropping `--def-lang`;
  an AnkiConnect error carries the action that failed and points at
  `--doctor`; an AnkiConnect timeout names the modal dialog that causes it
  nearly every time; an empty transcript says the video's captions are empty
  instead of naming a private function and a private dict key; and the three
  CLI paths that could fail without a next step now print one.

## [0.6.0] - 2026-08-27

Card quality: what is *in* the fields, rather than which fields exist.

### Added

- **The run summary names the words that got no definition**, instead of only
  counting them. On a real 406-card German run that is 28 words, 6.9% of the
  deck, and most are transcript damage or names (`Bissch`, `Herauszufinde`,
  `Barack`) that you would delete on sight. Naming them means finding and
  deleting them in one pass rather than meeting them during review.

  They are deliberately not filtered out. Three signals were measured against
  that deck and none separates them from real vocabulary: index absence drops
  real German compounds like `Rüberbringen`, prefix matching flags `Barack`
  as a truncation of `Baracke`, and 63% of words that *do* get a definition
  also appear only once. See issue #27.

- **Antonyms have an offline source of their own.** The field was the
  weakest on the card by a wide margin: 19.7% on a real 1054-lemma French
  deck against 98.6% for definitions. `make antonyms` builds a 4.3 MB index
  from ConceptNet covering 22 languages at once, and the field is filled
  from it when nothing else can.

  Measured end to end: French 19.7% to 34.8%, German 56.2% to 60.3%,
  Russian 47.8% to 48.8%. The asymmetry is the point. ConceptNet's
  antonyms are Wiktionary's own, re-extracted by a different tool: kaikki
  runs wiktextract, ConceptNet ran wikiparsec, and on the same edition they
  disagree about which words carry an antonym. The French index has one for
  15 045 words and ConceptNet has 12 376, comparable and overlapping only
  partly, so the union is much larger. German and Russian already hold eight
  to thirteen times more than ConceptNet does, so there it mostly hands back
  what is there. ADR-010.

  Entirely optional. Without the index every card is exactly what it was,
  and `--doctor` reports it as absent rather than missing.

### Changed

- **Translation setup installs CPU-only torch, cutting the install by 3.8 GB.**
  `argostranslate` needs `stanza`, which asks for `torch>=1.3.0` without
  naming a variant, so pip took the CUDA wheel and its `nvidia` and `triton`
  companions on every machine: 4.5 GB, 76% of the virtualenv, unusable
  without an NVIDIA card and a current driver. On the machine this was found
  on, which has an NVIDIA card, `torch.cuda.is_available()` was False the
  whole time because the driver was too old.

  Verified in a clean virtualenv: 733 MB instead of 4515 MB, with `nvidia`
  and `triton` absent entirely, and then applied to this machine's own
  environment, which went from 5.9 GB to 2.2 GB. A working GPU can still
  have the CUDA or ROCm build installed over the top, and neither the target
  nor the advice will replace one that is actually being used.
  ARCHITECTURE.md 8.41.

- **`make translate-setup` repairs an environment that already has the CUDA
  build**, instead of only helping a fresh one. Installing from the CPU
  index does nothing when torch is already present: pip answers "already
  satisfied" and the 4.5 GB stays. The target now detects a CUDA build with
  no usable GPU, replaces it with `--force-reinstall --no-deps`, and removes
  the `nvidia-*` and `triton` orphans that pip leaves behind on its own.

- **`--doctor` prints the repair commands in a form that runs.** They were
  spelled `pip`, which is not on `PATH` in a virtualenv built by `make venv`,
  and they replaced torch without removing the 3.4 GB of orphans, which is
  the worst of both. They now use the running interpreter's own path and
  name every installed `nvidia-*` distribution.

### Fixed

- **The definition cache could serve one language's examples to another.**
  The cache key recorded the language a definition was written in, not the
  language of the video it came from, and constraint 3.3 means those choose
  different content: a German run with `--def-lang en` writes a row holding
  German sentences. An English video looking up a spelling both languages
  share (`hand`, `arm`, `band`, `wild`) would have read that row back.
  Measured on a real 5408-row cache, 265 rows keyed `::en` already hold
  German examples; none had collided only because no English run had met one.

  The key is now `lemma::source::target::pos`, which also makes invalidating
  a language pairing a single `DELETE ... LIKE '%::de::en::%'` instead of a
  reconstruction through the vocabulary table.

  **Your existing cache is kept, not deleted.** Old rows move to a
  `definitions_v0` table and the live cache refills as words are met again.
  They cannot be rewritten, because recovering a row's source language means
  knowing which video it came from and nothing records that. See
  ARCHITECTURE.md 8.40.

- **CI checked a requirements file it never installed.** A Dependabot bump of
  `thinc` to 9.1.1 passed on all three Python versions while making
  `requirements.txt` unresolvable, because CI installs `pip install -e
  ".[dev]"` from `pyproject.toml` and never read the pinned files. A `pins`
  job now resolves both of them and checks each pin against the range
  `pyproject.toml` declares, which catches the case pip cannot: a pin that
  installs cleanly on its own but contradicts the project's own metadata.
  ARCHITECTURE.md 8.39.

### Dependencies

- spacy 3.8.14 to 3.8.15, requests 2.33.1 to 2.34.2, actions/checkout 4 to 7,
  actions/setup-python 5 to 7, and the dev group (black, ruff, mypy,
  types-requests) brought up to what the environment already ran.
- thinc stays at 8.3.13. spaCy 3.8 requires `thinc<8.4.0`, so it moves when
  spaCy does.

### Changed

- **Filler sounds no longer become cards.** `Ah`, `Bah`, `Ouai`, `Euh` and
  `Tss` were 3.4% of one real French run. spaCy tags them INTJ, which the
  pipeline already drops, but it tags them NOUN and ADV often enough in real
  sentences that they reached cards anyway.

  A per-language stoplist in `language.FILLER_SOUNDS` rather than a rule on
  the tag, because `Bonsoir` is also INTJ and is worth learning. Only sounds
  are listed: French `bon`, German `na` and Russian `ну` are common in speech,
  carry meaning, and are deliberately absent. Elongated spellings (`euuuh`,
  `ahhhh`) are folded onto the listed one, at runs of three characters rather
  than two so English `err` survives. Because folding at three maps `tsss`
  onto `ts` rather than `tss`, each sound's short spelling is derived from
  the table rather than written by hand, so no list can be complete-looking
  and still miss its own elongations.

  Lists ship for en, fr, de and ru, and only the French one has been counted
  against a real run. A language with no list filters nothing. Each run logs
  `Filler sounds skipped: N tokens`. See ARCHITECTURE.md 8.37.

  The bar for listing a sound is whether a course would teach it, not
  whether a speaker says it without thinking. Words that fail that bar are
  kept out on purpose, because the two mistakes are not equally cheap: a
  filler that slips through is one card you delete, while a word listed here
  by accident is one you are never offered and cannot tell is missing.

- Existing cards are untouched. The filter applies to the next run, not to a
  deck already imported.

- **Russian inflected words get a real definition.** A card could read
  "дательный падеж от кома", which points at another word instead of
  defining this one. Following the pointer already worked for German and
  French; Russian failed because the target carries a homograph
  disambiguator (`толк#(существительное I)`) or a stress mark (`нача́ло`),
  and neither matches a headword. Measured on the real index over 300
  sampled inflected forms, Russian went from 286 resolved to 298. German and
  French were already at 299 and 297 and are unchanged. ARCHITECTURE.md 8.38.

## [0.5.3] - 2026-08-17

The part of speech is written in the learner's language.

### Changed

- **Class is localised.** wiktextract normalises the part of speech to an
  English tag whichever Wiktionary edition an index was built from, so a
  German card read `noun` and a French one read `adj`. Cards now read
  `Substantiv` and `adjectif`.

  The label follows the definition, so it never disagrees with the text
  beside it: with `--def-lang fr` a German word reads `nom`, and without it
  the same word reads `Substantiv`. Constraint 3.3 already allowed `Class`
  to change language, and this is the reason it does.

  Labels for de, fr, ru, es, it, pt and en are in `language.POS_LABELS`.
  A language with no table falls back to English, which still expands `adj`
  into `adjective`, and a tag with no entry is shown unchanged rather than
  dropped. Adding a language is one row.

- Existing cards keep whatever they were imported with. Re-import to update
  them; nothing needs migrating and no review history is affected.

## [0.5.2] - 2026-08-17

Pronunciation audio plays inside the card instead of linking out.

### Added

- **Embedded audio.** Recordings are downloaded when the package is built and
  shipped inside the `.apkg`, so the Pronunciation field is now
  `[sound:...]` and plays in place. A link opened a browser, which is not
  reviewing; embedded audio also works on AnkiDroid and AnkiMobile and keeps
  working when the source is down.

  ADR-009 rejected this on size grounds and the estimate was wrong. Measured
  on real Commons files, De-Haus 16 KB, De-Spaziergang 30 KB, a 240-card
  German deck costs about 5 MB, not the "tens of megabytes" assumed.
  Wikimedia already serves them as MP3, so nothing is converted.

- `media.py`, with an on-disk cache (`MEDIA_DIR`, default `media/`) so a word
  met in a second video is not downloaded twice.

- **A link fallback per card.** When a download fails the card keeps the
  linked URL rather than losing the audio entirely. This is routine, not
  defensive padding: dictionaryapi.dev served one word's audio and returned
  502 for another in the same minute.

- **Paced downloads.** Audio requests are spread across the run at
  `MEDIA_RATE_LIMIT` per second (default 1.25) after a burst of
  `MEDIA_BURST`, and a `429` is retried for the period the server asks for.

  Without this the feature above barely worked. `upload.wikimedia.org`
  rate-limits per IP, about ten requests, then `429` with `Retry-After: 11`
 , so an 8-worker pool drained the allowance in under a second. A real
  406-card German run embedded **13** recordings and linked the other 364,
  with no error and a valid package. Going sequential did not help; only
  spacing the requests did. ARCHITECTURE.md 8.35.

- **Progress output while audio downloads.** Paced downloading of a few
  hundred files takes minutes, and the CLI previously sat silent at
  "Building Anki package...". The embedded-versus-linked count was already
  being logged at `INFO`, below the default `WARNING` level, so the one
  number that revealed the bug above was written and discarded.

### Fixed

- **The documented non-interactive recipe crashed.** `echo "s" | make run`
  supplies one line; the queued-word prompt consumed it and the import prompt
  then hit `EOFError`, ending the run with a traceback *after* the package had
  been written. All prompts now treat exhausted stdin as "no input" and take
  a safe default. CLAUDE.md 4.4.

### Known issues

- Images are ADR-009 **phase 3** and deliberately not built: sampling
  measured 2 of 5 usable, with `laufen` returning a coin from the town of
  Laufen. They need a relevance gate before they are worth having.

## [0.5.1] - 2026-08-16

Pronunciation now describes the word on the card, in every mode and in
English. No migration: the fields added in v0.5.0 are unchanged, and this
only alters what goes into them.

### Fixed

- **Cross-language mode put the wrong word's pronunciation on the card.**
  With `--def-lang`, `ipa` and `audio_url` were read from the *translated*
  word's index entry, so a German video with `--def-lang fr` showed `Haus`
  carrying maison's `\me.zɔ̃\` and a French recording. Examples, synonyms
  and antonyms were already gated against exactly this; pronunciation had
  been added beside the gate without being inside it.

  Pronunciation is now resolved once, by `_resolve_pronunciation(lemma,
  language)`, independent of which source supplied the definition. No
  definition branch touches it. See `ARCHITECTURE.md` 8.34.

### Added

- **English pronunciation.** dictionaryapi.dev returns real IPA (`/haʊs/`)
  and a complete audio URL in a `phonetics` block, on a call the pipeline
  already made and never parsed. English cards now carry pronunciation even
  when Merriam-Webster supplied the definition.
- Pronunciation on English fallback cards, through the same resolver.

### Changed

- `CLAUDE.md` §3.3 is now stated as a question, *does this field describe
  the word shown?*, rather than a list of three field names. Written as a
  list, it was violated three times, each by someone adding a fourth thing
  beside the gate.

## [0.5.0] - 2026-08-15

Pronunciation on cards, and a notetype that merges instead of forking.

> **Migration required.** This release adds two fields to the Anki notetype.
> `deck.ensure_model_fields()` runs automatically before the auto-import and
> adds them; a failed alignment cancels the import rather than risk a fork.
> Adding a field is non-destructive, verified on a real 2135-note
> collection with zero changed field values, but it is a schema change, so
> **Anki will ask for one full sync afterwards**. Importing by hand via
> File → Import bypasses the alignment and will fork the notetype.

### Added

- `IPA` and `Pronunciation` fields on cards, appended at indices 10 and 11
  (ADR-009 phase 1). Sourced from the offline Wiktionary index, which schema
  v2 extended with `ipa`, `audio_url` and `form_of` columns.
- Pronunciation on fallback cards, those with no definition from any
  source, which are the cards that benefit from it most.
- `deck.ensure_model_fields()`: aligns the collection's notetype with
  `cards.FIELDS` before importing, so appending a field merges rather than
  forking.
- `cards.FIELDS` as the single source of truth for card fields. The model is
  generated from it and both note builders address fields by name, so a
  misspelled field now raises instead of silently shifting every later one.
- `tests/test_hard_constraints.py`, one mutation-verified test per hard
  constraint in `CLAUDE.md` §3.
- `ROADMAP.md`: one goal per tag to v1.0.0, and an explicit list of what
  v1.0.0 freezes.
- Inflection-pointer following in the index, so `glaube` resolves to
  `glauben` for a definition while keeping its own pronunciation. On a real
  German video this cut pointer-glosses-as-definitions from 18 to 1.

### Changed

- **`ANKI_MODEL_ID` moved from `1607392319` to `1607392321`**, once and
  never again. The old value is the model ID from genanki's README example
  and never held any of this project's cards, Anki had already forked away
  from it. See `ARCHITECTURE.md` §8.31.
- `pyproject.toml` version now tracks the tag. It never has before: it read
  `0.1.0` at both v0.4.3 and v0.4.4, and `0.4.4` at v0.4.5.
- The coverage sweep (`scripts/coverage_matrix.py`) reports the two new
  fields; it previously measured indices 0–7 and stopped.

### Fixed

- The notetype was resolved by **name**, which is wrong on any collection
  with history: Anki suffixes the name when it forks, so the ID and the plain
  name come apart. On a real collection this would have added six fields to
  an unrelated 1134-note notetype. Now resolved by ID.
- Cross-language runs could take examples from the Merriam-Webster entry
  without the language gate, the one call site of three that
  `CLAUDE.md` §3.3's tests never inspected.

### Known issues

- English, and any language without a built index, gets no pronunciation.
  Fix identified and scheduled for v0.5.1.
- Audio coverage varies sharply by language: 95.0% of German index rows
  carry a recording, against 12.1% French and 4.4% Russian. IPA is
  dependable everywhere (83–99%).

## [0.4.5] - 2026-08-09

Correctness release, 67 commits.

- The deck duplicate check could not see most cards: it read a field named
  `Front` while the generated model's first field is `Word`. Measured at 0
  of 1036 notes visible in one real deck. Now reads the lowest-`order`
  field, which is what Anki treats as a note's identity.
- Cross-language definitions were broken four ways at once, each hiding the
  next, including `ARGOS_PACKAGES_DIR` pointing at an empty directory,
  which argostranslate reads itself.
- `importPackage` shared the 5-second timeout meant for quick queries, so
  imports that worked at 40k notes failed at 57k.
- A pasted YouTube URL aborted `make run` before the pipeline started: the
  video id was interpolated into printf's *format string*.

## [0.4.4] - 2026-08-05

- Circuit breaker for definition sources.
- WSL auto-import path translation, so AnkiConnect on the Windows side can
  resolve a package generated under `/mnt/c`.

## [0.4.3] - 2026-08-04

- Language-aware spaCy model selection (closes #3). An English model
  applying English morphology to French text had been misattributed to
  caption quality.

## [0.4.2] - 2026-08-04

- Synonym and antonym pill rendering fixes.

## [0.4.1] - 2026-08-04

- Card-quality and bug-fix release.

## [0.4.0] - 2026-07-19

- Multilingual definitions via the offline Wiktionary index, card redesign,
  adaptive CSS for Anki's light and dark themes, fuzzy matching improvements.

## [0.3.0] - 2026-07-13

- Card template redesign, multilingual definition fixes, translation timeout.

## [0.2.0] - 2026-07-08

- Language filter: subtitle selection by language code or deck name.

## [0.1.0] - 2026-07-02

- Initial working pipeline: YouTube transcript to Anki cards, 323 unit tests.

[Unreleased]: https://github.com/AlphaNerdFx/Tango/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.8.2...v0.9.0
[0.8.2]: https://github.com/AlphaNerdFx/Tango/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/AlphaNerdFx/Tango/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/AlphaNerdFx/Tango/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.5...v0.5.0
[0.4.5]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/AlphaNerdFx/Tango/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/AlphaNerdFx/Tango/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AlphaNerdFx/Tango/releases/tag/v0.1.0
