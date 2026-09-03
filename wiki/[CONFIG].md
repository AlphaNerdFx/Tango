# Configuration

All configuration is done through the `.env` file in the project root. Copy `.env.example` to `.env`, or run `make setup` for a guided walkthrough of the one setting most people actually want to change. Nothing described here is required to run the pipeline.

## Definitions

`MW_API_KEY`: Optional. Your Merriam-Webster Collegiate API key. Get one free at https://dictionaryapi.com/register/index.htm. Improves English definition and example quality; dictionaryapi.dev is used automatically without one, so the pipeline works fully with this left blank. The free tier allows 1000 requests per day. The SQLite cache means you rarely approach this limit in practice because words already fetched are never re-requested.

`DEFINITION_FETCH_WORKERS`: Maximum number of definition lookups running at once. Default is 5. Lower it if you hit rate limits on dictionaryapi.dev or Wiktionary, raise it if your network can take more.

`API_TIMEOUT`: Seconds to wait for a definition API response before timing out. Default is 8.

`CIRCUIT_BREAKER_THRESHOLD`: Consecutive failures against one definition source before the pipeline stops calling it for the rest of the run and falls back automatically. Default is 5.

## Proxy (optional, most users don't need this)

Most requests come from your own residential IP by default, which is exactly the traffic YouTube doesn't aggressively block. Only look at this section if you're actually getting a 429 error or connection timeout during transcript extraction.

This project doesn't recommend a specific provider. Webshare's free tier was tested here and made things worse, not better: transcript extraction failed with repeated 429s through the proxy but succeeded without it, because free-tier datacenter IPs are blocked more aggressively than residential ones. If you need a proxy, bring your own reputable one, a paid residential/mobile proxy you already trust, or a personal VPN.

`PROXY_HTTP_URL` and `PROXY_HTTPS_URL`: Your own proxy URLs.

`WEBSHARE_USERNAME` and `WEBSHARE_PASSWORD`: Alternative to the above, specifically for Webshare's credential format, if that's what you're using despite the note above.

## Anki connection

`ANKI_HOST`: The URL where AnkiConnect is listening. Default is `http://localhost:8765`. WSL users need to change this to their Windows host IP. See the WSL Setup page for details.

`ANKI_TIMEOUT`: How many seconds to wait for AnkiConnect before giving up. Default is 5.

## Language

Model selection per language lives in code (`language.SPACY_MODELS`), not in `.env`, `--language fr` (or a deck name spaCy can infer a language from) picks the right model automatically, with no configuration needed.

`SPACY_MODEL_SIZE_OVERRIDE`: Optional. Overrides every language's model size uniformly, e.g. set to `md` to request the medium model for whichever language is in use instead of each language's own default. Most languages default to the small model; French defaults to medium already, since the small French model was confirmed to mislemmatize some common conjugated verbs. Useful if you hit a similar accuracy problem in another language and want to test whether a bigger model helps, without editing code.

`LANGUAGE` and `DEF_LANG` are not `.env` variables. They're `make run` command arguments, not configuration you set once: `make run VIDEO_ID=<id> DECK="French" LANGUAGE=fr DEF_LANG=en`. Setting them in `.env` has no effect, since `.env` is read by the Python process, and by the time that happens the command line has already been parsed.

## Translation

`LIBRETRANSLATE_URL`: URL of a local LibreTranslate server. Leave blank to use community mirrors automatically. Set to `http://localhost:5000` if you have run `make translate-setup`.

`ARGOS_PACKAGES_DIR`: Stable path for argostranslate's downloaded models. Recommended on WSL, where the default path can move between sessions. See the WSL Setup page.

## Output

`DB_PATH`: Path to the SQLite database. Default is `pipeline.db` in the project root. Change this to an absolute path to avoid creating multiple database files when running from different directories.

`OUTPUT_DIR`: Directory for generated .apkg files. Default is `output/` in the project root.

`REVIEW_FILE`: Path to the deferred-word review file. Default is `review.json` in the project root.

## Deck duplicate matching

`CONFIDENCE_HIGH`: Fuzzy match score above which a word counts as already in the deck. Default is 90.

`CONFIDENCE_LOW`: Fuzzy match score below which a word counts as brand new. Default is 60.

`SHORT_WORD_THRESHOLD`: Words shorter than this use exact match only, since fuzzy matching is unreliable on short tokens. Default is 4.

## Never change these

`ANKI_MODEL_ID` and `ANKI_DECK_ID`, genanki identifiers baked into every card. Changing either after your first import makes Anki treat all existing cards as belonging to a different template or deck, permanently breaking review history. Leave these at their defaults unless you know exactly why you're changing them.
