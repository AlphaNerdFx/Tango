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

## Optional: translation support

If you want English definitions of non-English words, install the translation module.

```bash
make translate-setup
```

This installs argostranslate and PyTorch, which adds approximately 2GB to the installation size. Skip this if you only process English videos or want native-language definitions.