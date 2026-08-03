# Translation Mode

## What it does

By default, Tango fetches everything in the language of the video. A French video gives you French definitions, French examples, and French synonyms.

Translation mode lets you get the definition in a different language while keeping examples and synonyms in the original. The most common use case is studying French with English explanations: you see the French word, a French example sentence, and an English definition.

## How to enable it

Add `DEF_LANG=en` to your make command or `.env` file.

```bash
make run VIDEO_ID=xxx DECK="French" LANGUAGE=fr DEF_LANG=en
```

Or permanently in `.env`:

```
LANGUAGE=fr
DEF_LANG=en
```

## What changes on the card

With `DEF_LANG=en`:
- Definition: English explanation of the French word
- Class: English part of speech label
- 1st and 2nd Example Sentence: French examples from the dictionary
- Example from Youtube Video: French sentence from the video
- Synonyms: French synonyms
- Antonyms: French antonyms

## Translation sources

Tango translates the word before fetching the English definition. It tries sources in this order:

1. Community LibreTranslate mirrors (fast, no install needed, but may be unavailable)
2. Locally installed argostranslate model (reliable, requires one-time setup)

If neither is available, Tango prompts you once with three options: download the model now, continue without translation using native definitions, or exit.

## Installing the local translation model

```bash
make translate-setup
```

This installs argostranslate and downloads the language pair model for your LANGUAGE and DEF_LANG combination. Each model is approximately 150MB. Models are downloaded once and reused permanently.

Note: argostranslate pulls in PyTorch as a dependency, which adds approximately 1.5GB to your installation. This is expected. If disk space is a concern, use community mirrors and skip `make translate-setup`.

## Translation speed

Translation on CPU without GPU acceleration takes 10 to 60 seconds per word on first run, while the sentence boundary model loads. Subsequent words in the same session are faster. The pipeline enforces a 15-second timeout per word and creates a fallback card if the timeout is exceeded.