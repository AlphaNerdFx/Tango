# ADR-014: The local translation server is its own extra

Status: Accepted, 8 September 2026

Continues the numbering from ADR-013. This one is about install size, and
it belongs to the v0.12.0 rung, runs on modest hardware.

## What was measured

`pip install "tango-anki[translation]"` installed `libretranslate`, and
libretranslate is a **server**: a Flask application with a WSGI stack and a
document-conversion pipeline attached.

Measured 8 September 2026 in a clean virtual environment, the server pulls
**134 MB across 36 packages**. PyMuPDF is 65 MB of it, and lxml,
BeautifulSoup, Flask, waitress and werkzeug make up most of the rest. They
are there so LibreTranslate can translate PDFs and ODT files, which Tango
does not read and has never read.

That 134 MB was being paid by everyone who wanted translation, and almost
nobody wanted the server. Translation works through argostranslate models
running locally, and through whatever mirrors the user configures. Neither
needs anything on this list.

**It also corrected a claim in the roadmap.** The v0.12.0 rung listed
"drop pymupdf, 63 MB, zero references, no risk". Nothing in `src/` imports
it, which is what that was based on, but it is required by
`argos-translate-files`, which is required by `libretranslate`. It was not a
stray dependency anyone could delete. It was the cost of a feature nobody
had noticed was being installed.

## Options considered

**1. Leave it.** Rejected once the number was known. 134 MB is 40% of the
334 MB base install, for a component most users of the feature never start.

**2. Drop libretranslate entirely.** Rejected. Some people do want to run
their own instance, `make translate-setup` sets one up, and removing the
option to get a smaller number is solving the wrong problem.

**3. Split it into its own extra.** Chosen.

## The decision

`tango-anki[translation]` installs argostranslate and what it needs.
`tango-anki[translation-server]` adds libretranslate on top, for someone who
wants to run the server locally.

`make translate-setup` installs both, so the guided path a contributor
follows is unchanged and nobody who was relying on the server loses it
without being told.

## Consequences

- Translation costs 134 MB less for the people who do not run a server.
- The dependency is declared where it is honest: as the cost of the server,
  not the cost of translating a word.
- One more extra to explain in the README, which is a real cost and a small
  one.
- **The split landed after v0.11.0 was published**, so `translation-server`
  is not an extra the released package has. On 0.11.0 the command fails with
  an unknown extra and `[translation]` still installs the server as it
  always did. The README says so, and it arrives in v0.12.0.

## What this does not fix

The optional group keeps a dependency undeclared, not uninstalled. This is
the same limit CLAUDE.md 3.6 records about torch: once anyone runs
`make translate-setup`, all 134 MB is on the machine again, by their choice
and with the number in front of them. The extra makes the cost visible and
avoidable. It cannot make it smaller.
