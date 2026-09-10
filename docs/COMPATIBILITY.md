# Compatibility: what Tango promises not to break

Status: in force from v1.0.0, 11 September 2026.

Written during v0.12.0 and enforced from that release onwards, but the
promise only becomes binding at 1.0: under `0.x` this page described the
surface, and from 1.0 it constrains it.

This is the public surface SemVer talks about. **Nothing on this page may
change in a way that breaks an existing user without a major version bump.**
Everything not on this page may change in any release.

The page is not prose. `tests/test_compatibility.py` reads the tables below
and compares each one against the running code, in both directions, so a
surface that drifts fails the build rather than a user's setup. If you add a
command, an option, a setting or a card field, this page is part of the
change.

## 1. The notetype

| | value |
|---|---|
| MODEL_ID | 1607392321 |
| DECK_ID | 2059400110 |
| field count | 14 |

Field names and their order are frozen at indices 0 to 13:

| # | field | # | field |
|---|---|---|---|
| 0 | Word | 7 | Antonyms |
| 1 | Class | 8 | VideoID |
| 2 | Definition | 9 | Source |
| 3 | 1st Example Sentence | 10 | IPA |
| 4 | 2nd Example Sentence | 11 | Pronunciation |
| 5 | Example from Youtube Video | 12 | Image |
| 6 | Synonyms | 13 | Attribution |

`Word` stays at index 0. Anki treats a note's first field as its identity and
the duplicate check reads the lowest-order field.

New fields are **appended** at index 14 or higher, never inserted. Appending
is a schema change: `deck.ensure_model_fields()` has to add the field to the
collection's notetype before an import, or Anki forks a new notetype at a
bumped ID and strands every existing note. That has happened to this project
twice, which is why the ID gap from genanki's example is +2.

Changing `MODEL_ID` or `DECK_ID` destroys review history and cannot be
undone.

## 2. Commands

Twelve, and their names are frozen:

`backlog`, `build-antonyms`, `build-dictionary`, `doctor`, `install-model`,
`install-translation`, `languages`, `repair-images`, `review`, `run`,
`setup`, `uninstall`

## 3. Options

Frozen per command. An option may gain an alias; it may not be removed or
change meaning.

| command | options |
|---|---|
| run | `--deck` `--def-lang` `--force` `--images/--no-images` `--language` `--no-cache` `--verbose` |
| review | `--deck` `--def-lang` `--images/--no-images` `--language` `--verbose` |
| backlog | `--deck` `--def-lang` `--images/--no-images` `--language` `--verbose` |
| uninstall | `--dry-run` `--yes` |

Frozen on the top-level command: `--version`, `--help`,
`--install-completion`, `--show-completion`. `-h` is a frozen alias for
`--help`.

The two completion options arrived in v1.0.0 and are the only addition that
release carries. Typer provides them; they were switched off with no
recorded reason.

## 4. Exit codes

| code | meaning |
|---|---|
| 0 | the run did what was asked |
| 1 | a failure the tool has a message for |
| 2 | a usage error |
| 3 | a package was written, but not one card got a definition |

`tango doctor` returns 1 only when something is missing that stops a run.
Absent optional indexes are reported and return 0.

## 5. Configuration keys

Every key in `.env.example` is frozen, 36 of them. `.env.example` is the
authoritative list and is already checked against the code both ways: a key
the code reads that is missing from the file fails the build, and so does a
key in the file that nothing reads.

A blank value means unset, for every key. A key may gain a default; it may
not change meaning.

`--language` and `--def-lang` are options, not keys. There is no `LANGUAGE`
or `DEF_LANG` setting and there never was.

## 6. On-disk schemas

| store | version | cost of a bump |
|---|---|---|
| dictionary index | 2 | a full re-download per language: 288 MB de, 682 MB fr, 278 MB ru |
| antonym index | 1 | a 498 MB re-download, once, for every language |
| `pipeline.db` | migrated in place | the definition cache, expensive to rebuild |

`pipeline.db` gains columns in place and must keep doing so. The two indexes
carry a `user_version` and a bump discards the file, so bumping either is a
real cost to a real user rather than an internal detail.

## 7. Output

The package filename is `{video_id}_{YYYYMMDD_HHMMSS}.apkg`, written to
`OUTPUT_DIR`.

A package imports into a collection that already has this notetype without
forking it, provided the field list matches. That guarantee is the reason
item 1 exists.

## 8. How a frozen thing changes: the deprecation policy

Freezing a surface is only half a promise. The other half is saying how it
moves when it has to.

### The rule

**Anything on this page changes only in a major version.** After v1.0.0 that
means v2.0.0. Nothing here may break in a `1.x` release, however small the
break looks and however few users it appears to touch.

### The path out

A frozen thing is removed in three steps, never fewer:

1. **Deprecate.** It keeps working exactly as before. Using it prints a
   warning naming the replacement and the version it will be removed in. The
   deprecation is listed in `CHANGELOG.md` under the release that added it.
2. **Wait one minor release at minimum**, and longer if the thing is in a
   script rather than typed by hand. A user who runs Tango from cron finds
   out on their schedule, not ours.
3. **Remove**, in the next major version only.

A replacement must exist before step 1. "This is going away" without "use
this instead" is not a deprecation, it is a notice of breakage.

### What never gets a deprecation path

Two things are removed immediately when found, because leaving them is worse
than breaking them:

- **A security problem.** Fixed in the next release of any size, and
  described in `SECURITY.md`.
- **Something that never worked.** `LIBRETRANSLATE_URL` was documented in
  two places as a working setting and had been read into a constant nothing
  used since the day it was added. Deprecating it would have meant promising
  a second release of a setting that did nothing. ADR-012.

The second case has a test attached now, so the class cannot recur silently:
a setting resolved into a constant that nothing reads fails the build.

### What a break actually costs a user

This is the part that makes the policy real rather than procedural. The
costs are not uniform, and knowing which is which is the reason the freeze
list is grouped the way it is.

| what breaks | what it costs |
|---|---|
| `MODEL_ID` or `DECK_ID` | **every card's review history, permanently.** No undo. Years of scheduling |
| a field's name or position | content written into the wrong section of every new card, silently, with a valid package |
| the field list, without a migration | Anki forks a new notetype and strands every existing note on the old one |
| a command or option name | every script, alias and cron entry that used it |
| an exit code | any `&&` chain or CI step that branched on it |
| a configuration key | a silently ignored `.env` line, which looks applied and does nothing |
| the dictionary index schema | a full re-download per language: 288 MB German, 682 MB French, 278 MB Russian |
| the antonym index schema | a 498 MB re-download, once |
| `pipeline.db` | the definition cache, which is slow and rate-limited to rebuild |
| the `.apkg` filename pattern | anything that globbed for the output |

The top two rows are why item 1 of this page is item 1. Everything else on
that list costs someone an afternoon. Those two cost them their deck.

### What the 0.x line already broke, and what it cost

The policy above is written from what this project has actually done, not
from a template.

- **v0.5.0 added IPA and Pronunciation** to the notetype. Every collection
  had to be altered before importing. That is why it was a MINOR under the
  `0.x` rule rather than a patch, and it is the shape a post-1.0 field
  addition still takes: append, migrate, never insert.
- **v0.7.0 deleted the entire flag surface** and replaced it with
  subcommands, with no deprecation period at all. That was defensible
  exactly once, because the interface had no installed users: the package
  was not on PyPI until v0.8.0, three releases later. It could not be done
  today and the policy above exists to say so.
- **v0.8.1 existed only because v0.8.0's README shipped `make` instructions
  to people who had installed with pip.** PyPI freezes a description at
  upload, so correcting a published page costs a release. That is the
  clearest evidence for the rule in CLAUDE.md 18.11: a published artefact is
  frozen, so verify before uploading.
- **Eight user-facing messages named flags v0.7.0 had deleted**, found only
  at v0.8.0. Removing a spelling means removing it everywhere, including out
  of strings, and there is now a test that scans the package for the class.

### Under `0.x`, which is where this still is

Until v1.0.0 the rule is the one in `CLAUDE.md` section 15: a MINOR for new
capability **or anything requiring a migration**, a PATCH for fixes and for
finishing something already shipped. The deciding question is whether an
existing user has to do something, or whether something they already have
changes shape. If yes, it is at least a MINOR.

That is weaker than the post-1.0 rule on purpose, and it expires at v1.0.0.

## What is deliberately not frozen

- **Card content.** Definitions, examples, synonyms, antonyms, images and
  their sources may change in any release. They are expected to improve.
- **Log output and progress reporting.** Not an interface.
- **Anything private.** A leading underscore means it may change without
  notice, including `_`-prefixed functions in every module.
- **Coverage numbers, timings and sizes.** Measurements, not promises.
- **Which languages have an index built.** That is the user's machine.
