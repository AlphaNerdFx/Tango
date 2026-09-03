# ADR-008: Per-language dictionary sources for non-English definitions

Status: Proposed

Continues the numbering from `ADR_v0.4.0.pdf` (ADR-001 through ADR-007).
Written as markdown since no source file exists for that PDF to append to;
see the note at the end of this document about the two now needing to be
reconciled.

## Context

ADR-005 established the dual-source definition strategy: native-language
examples, synonyms, and antonyms from dictionaryapi.dev, definition and
part of speech from either Merriam-Webster (English) or dictionaryapi.dev
again (other languages). Issue #1 found this premise false for French.
A later multi-language testing pass (real videos, not word lists) found
it false for every non-English language tested, not just French:

| Language | Definitions found |
|---|---|
| English (control) | 269/274 (98%) |
| French | 0/1047 |
| German | 0/419 |
| Spanish | 0/279 |
| Portuguese | 0/126 |
| Japanese | 0/32 |
| Russian | 0/701 |
| Korean | 0/119 |
| Chinese | 0/552 |

Confirmed independently of this codebase with direct requests:
`de/Auto` 502, `es/casa` 502, `pt/casa` 404, `fr/maison` 404, `ja/水` 404,
`en/house` 200. `casa` and `maison` are "house" in Portuguese/Spanish and
French. dictionaryapi.dev works for English and effectively nothing else.

Two things are already shipped and are not what this ADR is about:

- Wiktionary's English-edition REST endpoint supplements native-language
  **example sentences** for fallback cards (ARCHITECTURE.md 8.13). It does
  not supply definitions, since the `definition` field in that response is
  an English gloss of the foreign word, not a native-language definition.
- WordNet supplements English synonyms/antonyms only (ADR-005's original
  scope). Not extended to other languages before this investigation.

This ADR is about the part that is still completely unaddressed for every
non-English language: **definitions, part of speech, synonyms, and
antonyms**.

## Candidates evaluated

Real testing against each, not vendor claims:

**PONS API.** Requires registration (a real onboarding cost issue #9
already flagged). Free tier caps at 1000 requests/month; a single video's
vocabulary can approach that alone. It is a bilingual translation
dictionary, not a monolingual native-language source, so a "definition"
from it is a translation pair, not a native-language explanation of the
word. Rejected.

**Open Multilingual Wordnet (via NLTK, already a dependency).** Real,
useful, but narrower than it first looks. Tested directly:

```
maison (fra): 24 synsets. lemma_names(lang='fra') = ['à_la_maison', 'maison']
              definition() = "at or to or in the direction of one's home..." (ENGLISH)
casa (spa):   14 synsets. lemma_names include casa, construcción, edificio...
              definition() is still the English Princeton WordNet gloss.
```

OMW only translates the lemma-to-synset mapping layer. The `.definition()`
and `.examples()` on every synset remain the original English Princeton
WordNet text regardless of the `lang=` parameter. Antonym relations tested
separately and came back empty for non-English lemmas (`lemma.antonyms()`
returns `[]` for French/Spanish in every case checked; the one non-empty
result found was a French lemma incorrectly pointing at an English antonym
object, not a real French antonym pair).

What OMW *does* give, for real: genuine native-language **synonym** words
within a matched synset (`syn.lemma_names(lang='fra')`), for 19 of our 24
supported languages -- als, arb, bul, cmn (Chinese), dan, ell, fin, fra,
heb, hrv, isl, ita, jpn, cat, spa, nld, nob, pol, por, ron, lit, slv, swe,
tha are present in the downloaded `omw-2.0` package; **de, ru, uk, mk, ko
are not covered at all**. Already an installed dependency (`nltk>=3.8`),
works offline, no API key, no rate limit. Note for whoever picks this up:
the currently-installed NLTK version needs the `omw-2.0` package, not
`omw-1.4` -- CLAUDE.md's setup instructions still say `omw-1.4`, which
downloads but is silently never used by this NLTK version. That doc fix is
tracked separately from this ADR.

**Wiktionary raw MediaWiki API (`action=parse&prop=wikitext`), not the
broken REST endpoint.** The REST `page/definition` endpoint only works
against the English edition (confirmed in 8.13's investigation; French,
German, Spanish editions return 501). The underlying raw wikitext API is a
different, older, universally-supported MediaWiki endpoint, tested
directly against three structurally different language editions:

French (`fr.wiktionary.org`, word "maison"):
```
# {{édifices|fr}} [[bâtiment|Bâtiment]] servant de [[logis]], d'habitation, de demeure.
#*{{exemple|lang=fr|Les maisons peuvent brûler...|source=...}}
```
A real French definition ("building serving as a dwelling") plus real
example citations, in the section under `=== {{S|nom|fr}} ===`.

German (`de.wiktionary.org`, word "Haus"):
```
{{Bedeutungen}}
:[1] zu einem bestimmten Zweck erbaute...
```
Real German definition data under a `{{Bedeutungen}}` (meanings) section,
different template name than French but the same underlying shape.

Russian (`ru.wiktionary.org`, word "дом"):
```
# архитектурное [[сооружение]], предназначенное для жилья...
{{пример|Просторный {{выдел|дом}}.}} ... |Пушкин}}
```
Real Russian definition ("architectural structure intended for
habitation") with example sentences, one attributed to Pushkin, under
`==== Значение ====` (meaning).

This works, and covers languages OMW does not (German and Russian
specifically, our two biggest current gaps). It is not free of cost,
though: each language edition uses its own section and template naming
convention (French: `{{S|nom|fr}}`, German: `{{Bedeutungen}}`, Russian:
`==== Значение ====`), so a single universal regex will not work across
all of them. A per-language-edition parser is needed, similar in kind to
the template-stripping this codebase already does for MW's `{bc}`/`{it}`
markup, but multiplied by however many language editions get supported.
Real work, but bounded and precedented, not an unknown quantity.

**Per-language official dictionaries (Larousse, DWDS, RAE, etc.).** Not
tested individually here since none of them offer a documented free public
API as far as this investigation found; historically scraping-only, which
carries the same ToS/reputational risk already reasoned through in issue
#8 for proxies. Not pursued further without a specific, confirmed API for
a specific language.

## Decision

Not made here. This ADR lays out the evidence and the real tradeoffs;
picking an approach (or a phased combination) is a decision for whoever is
driving this next, not something to lock in unilaterally in the same pass
that gathered the evidence.

The shape that evidence points toward, for consideration:

1. Extend the existing WordNet-supplementation pattern
   (`_wordnet_synonyms_antonyms` in `definition.py`) to also call OMW's
   `lang=` parameter for the 19 covered languages, since this is a small,
   low-risk addition to code that already exists and already works this
   way for English -- it would not fix definitions, only synonyms, for
   those 19 languages.
2. Build a Wiktionary wikitext parser as a new, separate source
   (`_fetch_from_wiktionary_wikitext` or similar), starting with 2-3
   languages to validate the per-edition parsing approach before
   committing to covering all 24, given each language edition needs its
   own parsing logic. This is the only evaluated path to real
   native-language definitions, and the only one that covers German and
   Russian.
3. Leave per-language official dictionaries (Larousse/DWDS/RAE) out of
   scope until a specific one is confirmed to have a real, free,
   documented API -- none evaluated here does.

## Consequences (of the evidence, not yet of an implementation)

Whichever path is chosen, "coverage varies by language" (ADR-005's
original framing) is no longer an accurate description of the current
state. It is closer to "non-English definitions do not exist," and that
should be reflected in how this project describes itself publicly
(README, wiki) until fixed, not softened.

Any Wiktionary-based fix inherits the same Wikimedia rate-limit
constraints already handled for the example-sentence source (8.13): a
429 must count as a circuit-breaker failure, not a 404-style "healthy but
absent" result, and concurrent fetches need the same care.

---

**Note on ADR numbering:** this document has no markdown source to merge
with `ADR_v0.4.0.pdf` (ADR-001 through ADR-007); that file appears to be
authored directly as a PDF with no tracked source. Either fold this
document's content into a regenerated `ADR_v0.5.0.pdf` by whatever process
originally produced the `.pdf`, or move the ADR log to markdown going
forward and treat the PDF as a frozen historical snapshot. Worth a
decision, not assumed here.
