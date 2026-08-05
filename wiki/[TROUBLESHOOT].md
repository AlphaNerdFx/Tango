# Troubleshooting

## Transcript extraction fails with 429 or connection timeout

YouTube is rate-limiting your IP address. This is usually temporary and tied to the volume of requests from one IP — most individual users running Tango from their own home connection won't hit it under normal use.

If it persists: Webshare's free tier is confirmed to make this *worse*, not better (its datacenter IPs are blocked more aggressively than residential ones — don't use it). If you need a proxy, bring your own reputable one (a paid residential/mobile proxy, or a personal VPN) and set `PROXY_HTTP_URL`/`PROXY_HTTPS_URL` in `.env`. The project doesn't recommend a specific provider.

## AnkiConnect not reachable

Check that Anki is open and AnkiConnect is installed (code: 2055492159 in Tools -> Add-ons -> Get Add-ons). After installing AnkiConnect, Anki must be fully restarted, not just closed and reopened.

If you are on WSL, see the WSL Setup page.

Run this to test connectivity:

```bash
curl http://localhost:8765
```

If you get "connection refused", AnkiConnect is not listening.

## pipeline.db errors about missing columns

The `definitions` table already self-migrates on connect (it adds missing columns automatically), so this shouldn't happen on a current install — if you see it, you're likely on an old version; pull the latest code first. As a last resort, deleting `pipeline.db` does recreate the schema, but note this loses more than "processing history": it also discards the `vocabulary` table (which video introduced which word, frequency, first-appearance position) and the definition cache (every word gets re-fetched from the network on the next run). Your actual Anki cards are unaffected either way — they live in Anki's own collection, not in `pipeline.db`.

## Translation model downloads every time

The argostranslate model is being installed to a path that changes between sessions. This is a known issue in WSL environments where the home directory path is inconsistent. Add this to your `.env`:

```
ARGOS_PACKAGES_DIR=/mnt/c/Users/YourUsername/.argos-translate
```

Replace `YourUsername` with your actual Windows username. This pins the model directory to a stable Windows path.

## Cards have no example sentences

This usually means dictionaryapi.dev returned no examples for those words in the target language. Coverage varies by language. For English vocabulary, Merriam-Webster provides better example coverage. For other languages, many words will only have the transcript example sentence.

## Same word appears multiple times as different cards

This happens if you run the pipeline on the same video twice without `pipeline.db` tracking the first run, or if the video ID in the GUID changed. Delete duplicate notes in Anki via Browse -> select duplicates -> delete.

## Processing is very slow

Each word requires at least one API call to a definition source. Since v0.4.5, up to 5 of these run concurrently through a bounded thread pool instead of one at a time, so a 100-word vocabulary extraction should be well under the old 2-12 minute range depending on cache state and how the dictionary APIs are responding that day. Words already in the SQLite cache are instant. Second runs of similar videos are significantly faster.

If you want more or fewer concurrent lookups, set `DEFINITION_FETCH_WORKERS` in your `.env` file. Lower it if a dictionary API starts returning errors under load, raise it if your network can take more.

Translation mode adds additional latency per word for the translation step.