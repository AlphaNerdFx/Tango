# Tango

Turn a YouTube video's transcript into an Anki flashcard deck.

Give it a video ID and a deck name. It pulls the subtitles, finds the
vocabulary, skips what is already in your deck, fetches a definition, two
example sentences, pronunciation audio and a picture, and writes an
importable `.apkg`.

This image exists for the "just run it" case: no Python setup, no
virtualenv, no spaCy download step.

```bash
docker run --rm -v "$PWD/data:/data" \
  yousseflarbi/tango run dQw4w9WgXcQ --deck "French" --language fr
```

The package lands in `data/output`.

## Tags

| tag | what it is |
|---|---|
| `latest` | the most recent release |
| `0.11.0` | pinned, and what `latest` currently points at |

The image installs `tango-anki` from PyPI rather than building from the
source tree, so it exercises the same artefact anyone else installs.

## Mount /data, or lose your work

Everything lives in `/data`: the definition cache, the dictionary indexes,
the downloaded audio and pictures, and the generated packages. It is a
volume. Without mounting it, every run starts with an empty cache and the
`.apkg` disappears when the container exits.

## Anki runs on your machine, not in here

AnkiConnect binds to `127.0.0.1`, which a container cannot reach. A run
without a route to it still works: every word goes to the backlog and you
still get a package, you just do not get the duplicate check against your
existing deck.

To give it that route, the image already points `ANKI_HOST` at
`host.docker.internal`, so all you add is the route itself:

```bash
docker run --rm -v "$PWD/data:/data" \
  --add-host host.docker.internal:host-gateway \
  yousseflarbi/tango run <video-id> --deck "French"
```

On native Linux, `--network host` works instead and needs no `--add-host`.

## Languages

English works with no setup: `en_core_web_sm` is baked in. Other languages
need a model, which installs into your mounted volume and stays there:

```bash
docker run --rm -v "$PWD/data:/data" yousseflarbi/tango install-model fr
```

Twenty-four models are not baked in on purpose. They would multiply the
image size for something most users never ask for.

Non-English definitions also want the offline Wiktionary index, which is a
large one-time download per language and then works offline:

```bash
docker run --rm -v "$PWD/data:/data" yousseflarbi/tango build-dictionary fr
```

## Check the setup

```bash
docker run --rm -v "$PWD/data:/data" yousseflarbi/tango doctor
```

It reports models, indexes, whether AnkiConnect is reachable, and prints the
command that fixes anything missing.

## Pictures on cards

Off by default, because a picture roughly doubles the size of a deck that
already carries audio. Ask for them per run:

```bash
docker run --rm -v "$PWD/data:/data" \
  yousseflarbi/tango run <video-id> --deck "French" --language fr --images
```

They go on concrete nouns only. The word is resolved to a Wikidata concept
rather than searched as text, refused where the concept is abstract, and
dropped where the picture would describe a different sense than the card's
own definition. Every picture carries the credit its licence requires.

## Notes

Runs as a non-root user, so files written into your mounted volume stay
deletable. Uses `tini`, so ctrl-c during a long definition phase stops the
run instead of detaching it.

Source, full documentation and issues: https://github.com/AlphaNerdFx/Tango

MIT licensed.
