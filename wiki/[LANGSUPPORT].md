# Language Support

## How language is resolved

Tango resolves the target language in this order:

1. The `--language` flag (or `LANGUAGE=` in make commands) always wins if provided.
2. If no flag is given, Tango reads the deck name. A deck called "French" or "Deutsch" or "Francais" is automatically recognised.
3. If neither works, Tango raises an error and tells you exactly what to do.

## Supported languages

Run this command to see all 40 supported languages with their BCP-47 codes.

```bash
tango languages
```

Supported languages include French, Spanish, German, Italian, Portuguese, Russian, Polish, Japanese, Chinese (Simplified and Traditional), Korean, Arabic, Hindi, Turkish, and more.

## Language name variants

The deck name matcher recognises multiple names for each language. For French, any of these work: French, Français, Francais, Frances, Französisch, the point is it checks the language's name as spelled in English, French, Spanish, German, and the language's own name.

Names are case-insensitive. Sub-deck notation is handled: `Language::French::B2` extracts "French" correctly.

## Definition language

By default, definitions come from the same language as the video. A French video gives French definitions.

To get English definitions of French words, add `DEF_LANG=en` to your make command or `.env` file.

```bash
make run VIDEO_ID=xxx DECK="French" LANGUAGE=fr DEF_LANG=en
```

Example sentences, synonyms, and antonyms always come from the original language regardless of DEF_LANG.

## Translation mode

When DEF_LANG differs from LANGUAGE, Tango translates the word before fetching the English definition. Translation uses community LibreTranslate mirrors first, then a locally installed argostranslate model.

Run `make translate-setup` to install the local model for your language pair. Models are about 150MB each and are downloaded once.

## Coverage notes

dictionaryapi.dev's non-English coverage is effectively broken, not just "varies by language." Verified with direct API checks: French lookups for extremely common words (`eau`, `chat`, `maison`) return 404, and a real French-video run produced 0 definitions found out of 108 native-language lookups attempted. English coverage is reliable; expect non-English videos to produce mostly fallback cards (word, no dictionary definition, no synonyms or antonyms) until this is fixed.

Since v0.4.5, those fallback cards get a real dictionary example sentence where possible: a Wiktionary lookup supplements the example when dictionaryapi.dev has nothing. In a real French video test this brought the fallback-card example rate from 0 percent to over half, and cut the number of words dropped entirely (no definition, no dictionary example, no transcript match) by roughly two-thirds. Definitions, part of speech, synonyms, and antonyms are unaffected by this and remain missing for non-English languages. Tracked as [issue #1](https://github.com/AlphaNerdFx/Tango/issues/1).