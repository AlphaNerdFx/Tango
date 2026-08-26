# FAQ

## Why does Tango use Anki and not another flashcard app?

Anki's spaced repetition algorithm is the most studied and widely used in language learning. Its open .apkg format means cards are portable and not locked into a service. AnkiConnect provides a local API that makes automation possible without scraping or browser automation. The plan is to expand to other platforms after v1.0.0.

## Why does it take so long to process a video?

Each vocabulary word requires at least one API call to a definition source. As of v0.4.5, up to 5 of these calls run at once through a bounded thread pool, so a 100-word video with a cold cache is much faster than the old one-at-a-time approach. Words already in the SQLite cache are instant, so repeated runs on similar content are faster still. The concurrency limit is configurable via the `DEFINITION_FETCH_WORKERS` environment variable if you want to trade speed for a gentler load on the dictionary APIs.

## Why are antonyms often missing?

Free dictionary APIs rarely return antonyms. dictionaryapi.dev includes them occasionally. Merriam-Webster's free tier almost never does. Tango supplements antonyms from WordNet when the APIs return nothing, which improves coverage for English, and from the offline Wiktionary index for other languages.

If you have not run `make antonyms`, run it. It builds a 4.3 MB index from ConceptNet covering every supported language, and it is the last source tried when nothing else filled the field. Measured on real decks it takes French from 19.7% to 34.8% and German from 56.2% to 60.3%.

Antonyms remain the thinnest field even so. Roughly two words in three still have none, because Wiktionary simply does not record one for most words.

## Why is my deck not recognised by name?

The deck name matcher checks each word in the deck name against a list of 40 languages in multiple languages. If your deck is named something that does not contain a recognisable language name, it will not be matched. Use the explicit `LANGUAGE=` flag instead, or rename your deck to include the language name. Run `python -m pipeline --list-languages` to see all recognised names.

## Can I use Tango without an internet connection?

Not fully. Transcript extraction requires YouTube to be reachable. Definition fetching requires the MW or dictionaryapi.dev APIs. Translation can work offline if a local argostranslate model is installed. The SQLite cache means repeat words do not need internet access.

## What happens to words with no definition?

Tango creates a fallback card with just the word and the sentence from the video where it appeared. The Definition field shows "No definition found". These cards are tagged `no-definition` so you can filter or delete them in Anki if you prefer.

## Can I reprocess a video I already ran?

Yes, pass `--force`. Without it, the pipeline checks `pipeline.db` and warns you that the video was already processed, then exits without creating cards. `--force` skips that check and reprocesses the video, though words already in the target deck are still skipped by the normal deck duplicate check, so you will only get new cards for genuinely new vocabulary. You can still delete the video's entry from `processed_videos` in the SQLite database directly if you prefer that route.

## Why are proper nouns excluded?

Names of people, places, and organisations rarely appear in dictionaries and almost never have antonyms or synonyms in the useful sense. Including them creates cards like "Paris: a city in France" which adds noise without helping you learn the language. spaCy's named entity recognition tags these automatically, so they are filtered before the deck check step.