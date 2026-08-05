# Configuration

All configuration is done through the `.env` file in the project root. Copy `.env.example` to `.env` and fill in the values you need.

## Required

`MW_API_KEY` — Your Merriam-Webster Collegiate API key. Get one free at https://dictionaryapi.com/register/index.htm. The free tier allows 1000 requests per day. The SQLite cache means you rarely approach this limit in practice because words already fetched are never re-requested.

## Proxy (optional)

`WEBSHARE_USERNAME` and `WEBSHARE_PASSWORD` — Webshare proxy credentials. Only needed if YouTube blocks your IP address, which shows up as a 429 error or connection timeout during transcript extraction. Get a free account at https://webshare.io with 10 proxies and 1GB per month.

## Anki connection

`ANKI_HOST` — The URL where AnkiConnect is listening. Default is `http://localhost:8765`. WSL users need to change this to their Windows host IP. See the WSL Setup page for details.

`ANKI_TIMEOUT` — How many seconds to wait for AnkiConnect before giving up. Default is 5.

## Language

`SPACY_MODEL` — The spaCy model to use. Default is `en_core_web_sm`. Options are `en_core_web_sm` (fast), `en_core_web_md`, and `en_core_web_lg` (most accurate). After changing this, run `make spacy-model` to download the new model.

`DEF_LANG` — The language for definitions. Leave blank to get definitions in the same language as the video. Set to `en` to get English definitions of non-English words.

## Translation

`LIBRETRANSLATE_URL` — URL of a local LibreTranslate server. Leave blank to use community mirrors automatically. Set to `http://localhost:5000` if you have run `make translate-setup`.

## Output

`DB_PATH` — Path to the SQLite database. Default is `pipeline.db` in the project root. Change this to an absolute path to avoid creating multiple database files when running from different directories.

`OUTPUT_DIR` — Directory for generated .apkg files. Default is `output/` in the project root.

`DEFINITION_FETCH_WORKERS` — Maximum number of definition lookups running at once. Default is 5. Lower it if you hit rate limits on dictionaryapi.dev or Wiktionary, raise it if your network can take more.