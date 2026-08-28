# HANDOVER

Written 28 August 2026.

## Where the tree is

| | |
|---|---|
| branch | `main` |
| last commit | `7b052bc` docs: every instruction now uses the tango subcommands |
| `git status` | clean, nothing staged, nothing untracked |
| remote | in sync with `tango-origin/main`, 0 ahead |
| `make check` | **exit 0** |
| tests | **1005 passing, 24 integration deselected** |
| `__version__` | `0.6.0`, matching tag `v0.6.0` |

No background jobs are running. Nothing is half-applied; every change below is
committed and pushed.

## What shipped this session

**v0.6.0 released** (tag `v0.6.0`, GitHub release "Card quality"). All five
rung items done, `__version__`, the tag and CLAUDE.md agreed at the moment of
tagging, and all 14 tags now have release notes.

**The antonym field got an offline source** (ADR-010). ConceptNet index,
`make antonyms`, 4.3 MB for 22 languages. French 19.7% to 34.8% measured end
to end.

**A wrong explanation was corrected across eight files** (`d3fc21c` through
`e0024f9`). The antonym gain was attributed to a Wiktionary *edition* gap. It
is an *extractor* gap: kaikki runs wiktextract, ConceptNet ran wikiparsec, on
the same edition. The measurements were always right; only the cause was
wrong. Left visible in ADR-010 and 8.42 rather than quietly rewritten.

**Failures name their fix** (`6c4c20e`–`e8a9b8a`). Seven messages that stopped
a run while stating only the outcome: `--force`, `--install-translation`, the
AnkiConnect action and modal dialog, and an empty-transcript message no longer
written at whoever wrote the call site.

**Merriam-Webster is paced and a stopped source is reported** (`647a2fe`–
`2c920af`, ARCHITECTURE 8.43). A real 1094-word English run shipped 167 cards
with a definition and 927 without, and said "Done". `media.RateLimiter` is now
shared rather than copied; the run summary names any source the breaker gave
up on.

**One word no longer becomes two cards** (`9f6cdcd`, 8.44). 15 of 1079 French
cards were an inflected form of another card. Folded via the index's `form_of`
pointer, and only when the base form is in the same run, so 8.30's rule that
the learner sees the form they met is untouched.

**English has an offline index** (ADR-011, `fe68277`–`d084289`). Reverses
8.19's exclusion, which was measured on 7 August, a week before IPA and
Pronunciation became card fields. 1 486 439 entries, 236 MB. On a real deck:
IPA 0% to 96.4%, audio 0% to 97.0%, examples 15% to 90.3%.

**Progress and timing** (`fcc9352`, `b358e21`). Per-phase elapsed, and a
progress line for the definition phase that redraws on a terminal and prints
one line per decile into a log file.

**argparse to Typer** (`7fdc085`–`82f03d0`). `tango run <id> --deck "..."`,
plus `review`, `backlog`, `languages`, `doctor`, `setup`, `install-model`,
`install-translation`, `build-dictionary`, `build-antonyms`. **A console entry
point exists for the first time** — there was no `[project.scripts]` at all,
so even an editable install gave you `python -m pipeline`.

**The ladder was resequenced** (`f9a61b8`, `a3ede7b`). Packaging moved from
v0.10.0 to **v0.8.0**, ahead of cross-platform support and install size, after
an outside review pointed out the funnel has no top. `ANKI_HOST` and a thin
default install moved with it.

## In progress

Nothing is mid-change. **The v0.7.0 rung is complete**: error messages,
progress and timing, and the Typer migration are all done and pushed, and
`__version__` still reads `0.6.0`.

**The exact next step** is a decision, not code: tag v0.7.0, or roll its three
items into v0.8.0 and tag once when the package is publishable. Given
packaging moved to v0.8.0 and the CLI has no installed users, tagging v0.7.0
mainly buys a clean release note.

After that, v0.8.0 in this order:

1. **Decide the distribution name.** `tango` on PyPI is an unrelated project;
   `pyproject.toml` still says `yt-anki-pipeline`. The console command is
   already `tango` and does not have to match the distribution name.
2. **Thin the default install.** Translation and its ~950 MB should be
   `pip install <name>[translate]`, not the default.
3. **`ANKI_HOST` off WSL.** It defaults to a gateway IP meaningless anywhere
   else, and a package that cannot reach Anki is not installable in any
   useful sense.
4. Publish, then first-run model/index download, Dockerfile, real uninstall.

## Open decisions

| decision | why it is waiting |
|---|---|
| **Tag v0.7.0 or fold into v0.8.0** | Release call, see above |
| **The PyPI distribution name** | Needs you. Gets more expensive every tag and link |
| **French fixed expressions** | `d'accord` becomes `accord`. Measured at 7 of 1079 cards and six are legitimate words (`ici`, `après`, `autant`). I recommended against dropping post-elision tokens because it would delete `ici`; the feature version, teaching `d'accord` itself, needs a hand-curated per-language list. Recorded in TASKS.md, not started |
| **Transcript fallback** | The whole pipeline depends on one extraction path. I suggested accepting a user-supplied subtitle file before considering ASR, since Whisper collides head-on with the install-size goal. No ADR written yet |
| **Learned queue matching and proficiency levels** | Parked beyond v1.0.0 in ROADMAP §4 and TASKS.md. One part is cheap and time-sensitive: nothing records what the user answers at the y/n/s prompt, so the training data is being discarded on every run |

## Known-broken

Nothing. `make check` exits 0 and no test is skipped or xfailed beyond the 24
integration tests excluded by default.

Two environmental notes that are not this repository's bugs:

- **dictionaryapi.dev returned HTTP 522 all of 27 August.** It is still
  English's only source of example sentences. Definitions and pronunciation
  no longer depend on it now that the English index exists.
- **`site-packages` holds `~ip` and `~ip-26.1.2.dist-info`**, 11 MB of
  leftovers from an interrupted pip self-upgrade, which is what prints
  `WARNING: Ignoring invalid distribution -ip` on every pip command. TASKS.md
  records the decision to leave them; deleting them is safe if the noise
  becomes annoying.

## Conventions worth not relearning

- `make check` must exit 0 before any commit; a hook enforces it.
- One commit per file, subject line only, conventional prefix.
- Write the code, get it reviewed, **then** write tests. Mutation-verify every
  new test by breaking the target and confirming the test fails.
- Docs ship with the change, not on a second request.
