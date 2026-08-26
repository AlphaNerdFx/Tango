# Installation

## Prerequisites

- Python 3.10 or later
- Anki desktop (https://apps.ankiweb.net)
- AnkiConnect add-on installed in Anki (code: 2055492159)
- Git

## Setup

Clone the repository and run the setup command.

```bash
git clone https://github.com/AlphaNerdFx/Tango.git
cd Tango
make all
```

`make all` does three things: creates a virtual environment in `.tangovenv/`, installs all Python dependencies, and downloads the spaCy English language model. This takes a few minutes on first run.

Copy the environment file and fill in your API keys.

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in `MW_API_KEY` at minimum. Get a free Merriam-Webster API key at https://dictionaryapi.com/register/index.htm.

## Verify the installation

```bash
make test
```

All unit tests should pass. If they do not, open an issue with the error output.

## First run

```bash
make run VIDEO_ID=dQw4w9WgXcQ DECK="English"
```

Replace `dQw4w9WgXcQ` with any YouTube video ID that has English captions. Replace `English` with the name of an existing Anki deck.

## Optional: better antonyms

Antonyms are the weakest field on a card. One index covers every language, unlike the dictionaries, so this is a single command:

```bash
make antonyms
```

The download is 498 MB and is streamed through a filter rather than saved, so what stays on disk is 4.3 MB. Measured on real decks it takes French antonym coverage from 19.7% to 34.8%, German from 56.2% to 60.3% and Russian from 47.8% to 48.8%.

Skip it and nothing breaks: every card is exactly what it would have been. `python -m pipeline --doctor` reports whether it is built.

## Optional: translation support

If you want English definitions of non-English words, install the translation module.

```bash
make translate-setup
```

This installs argostranslate and PyTorch, which adds roughly 950 MB to the installation, 725 MB of it PyTorch (measured 26 August 2026). Skip it if you only process English videos or want native-language definitions.

PyTorch comes in a CPU build and a CUDA build, and pip picks the CUDA one by default even on a machine with no NVIDIA card. That build is 4.5 GB once `nvidia` and `triton` come with it, so `make translate-setup` installs the CPU build deliberately. Run `python -m pipeline --doctor` if you want to know which one you have.