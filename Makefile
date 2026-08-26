# =============================================================================
# yt-anki-pipeline — Makefile
# =============================================================================
# Targets:
#   make all          — full first-time setup (venv + install + spaCy model)
#   make venv         — create virtual environment
#   make install      — install package and all dependencies into venv
#   make spacy-model  — download the spaCy model for SPACY_LANG (default: en)
#   make doctor       — report what is installed and what is missing
#   make test         — run unit tests only (no network, no Anki required)
#   make test-all     — run full suite including integration tests
#   make coverage     — run unit tests with a per-module coverage report
#   make format       — auto-format source and test files with black
#   make lint         — check code style with ruff
#   make typecheck    — static type checking with mypy
#   make run          — run the pipeline (VIDEO_ID and DECK required;
#                       optional LANGUAGE, DEF_LANG, FORCE=1)
#   make review       — process the review.json file (optional LANGUAGE, DEF_LANG)
#   make backlog      — process the Anki backlog for a deck (optional LANGUAGE, DEF_LANG)
#   make clean        — remove venv, output, cache files
#   make check-os     — warn if running on Windows without a compatible shell
# =============================================================================

# -- Configuration ------------------------------------------------------------

PYTHON        := python3
VENV_DIR      := .tangovenv
VENV_PYTHON   := $(VENV_DIR)/bin/python
# `python -m pip`, not the bin/pip console script. The script can go missing
# while pip itself is perfectly fine: an interrupted `pip install --upgrade
# pip` leaves the old tree orphaned as site-packages/~ip and never restores
# the script, which is exactly what happened in this repo's venv. `install`
# and `translate-setup` both died on it with "No such file or directory".
# The module form works whenever pip is importable, which is the condition
# that actually matters.
VENV_PIP      := $(VENV_PYTHON) -m pip
VENV_ACTIVATE := $(VENV_DIR)/bin/activate

SPACY_LANG    ?= en
MIN_PYTHON    := 3.10

# Every printf below that shows a user-supplied value passes it as a %s
# ARGUMENT, never inside the format string. A YouTube URL ends in things like
# %3D, and `printf "video: $(VIDEO_ID)\n"` made printf read that as a format
# directive: "printf: %3D: invalid directive", recipe aborts with Error 2, and
# the pipeline never ran at all. The user saw an empty deck.
#
# Pipeline run defaults — override from CLI:
#   make run VIDEO_ID=LV_NoD2M54w DECK="Language::English"
VIDEO_ID      ?=
DECK          ?=

# -- OS detection -------------------------------------------------------------
# COMSPEC is set on native Windows CMD and PowerShell.
# MSYSTEM is set by Git Bash; WSLENV is set by WSL.

UNAME := $(shell uname -s 2>/dev/null || echo Windows)

# -- Colour helpers -----------------------------------------------------------

RESET  := \033[0m
BOLD   := \033[1m
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
CYAN   := \033[36m

# -- Phony targets ------------------------------------------------------------

.PHONY: all venv install setup spacy-model dictionary translate-setup translate-stop \
        test test-all coverage format lint typecheck translate-model doctor \
        run review backlog clean check-os help

.DEFAULT_GOAL := help

# -- check-os -----------------------------------------------------------------

check-os:
ifdef COMSPEC
ifndef MSYSTEM
ifndef WSLENV
	@printf "$(YELLOW)$(BOLD)[warn]$(RESET)  Windows detected without Git Bash or WSL.\n"
	@printf "\n"
	@printf "  This Makefile requires a Unix-compatible shell.\n"
	@printf "  Please use one of the following:\n"
	@printf "    * Git Bash  (https://git-scm.com/downloads)\n"
	@printf "    * WSL       (https://learn.microsoft.com/en-us/windows/wsl/install)\n"
	@printf "    * Cygwin    (https://www.cygwin.com)\n"
	@printf "\n"
	@exit 1
endif
endif
endif
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Shell environment looks compatible.\n"

# -- all — first-time setup ---------------------------------------------------

all: check-os venv install spacy-model
	@printf "\n"
	@printf "$(GREEN)$(BOLD)Setup complete.$(RESET)\n"
	@printf "  Run the pipeline with:\n"
	@printf "    $(CYAN)make run VIDEO_ID=<id> DECK=\"<deck name>\"$(RESET)\n"
	@printf "\n"

# -- venv ---------------------------------------------------------------------

venv: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Creating virtual environment in $(VENV_DIR)/\n"
	@if [ ! -d "$(VENV_DIR)" ]; then \
		$(PYTHON) -m venv $(VENV_DIR); \
		printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Virtual environment created.\n"; \
	else \
		printf "$(YELLOW)$(BOLD)[warn]$(RESET)  $(VENV_DIR)/ already exists — skipping creation.\n"; \
	fi
	@$(VENV_PYTHON) -c \
		"import sys; v=sys.version_info; \
		exit(0) if (v.major,v.minor)>=(3,10) \
		else print('Python $(MIN_PYTHON)+ required, found '+str(v.major)+'.'+str(v.minor)) or exit(1)"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Python version check passed.\n"

# -- install ------------------------------------------------------------------

install: venv
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Installing dependencies...\n"
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -e ".[dev]"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Dependencies installed.\n"

# -- setup ----------------------------------------------------------------
# Guided .env setup for the one optional credential worth walking a
# non-technical user through (issue #9). Nothing this configures is
# required -- dictionaryapi.dev works with zero setup.

setup: venv
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline --setup

# -- doctor -------------------------------------------------------------------
# Reports what is installed and what is missing, with the command to fix each.
# Start here when something is not working: nearly every failure investigated
# in this project turned out to be setup rather than logic, and none of it was
# visible from the failure itself.

doctor: venv
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline --doctor

# -- dictionary ---------------------------------------------------------------
# Builds the offline Wiktionary index for one language. Large one-time
# download; the result is gitignored and rebuildable at any time. This is
# what gives non-English runs real native-language definitions -- no online
# source has them (issue #1).

dictionary: venv
	@if [ -z "$(LANGUAGE)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  LANGUAGE is required.\n"; \
		printf "  Usage: $(CYAN)make dictionary LANGUAGE=fr$(RESET)\n"; \
		exit 1; \
	fi
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline --build-dictionary "$(LANGUAGE)"

# -- translate-model ----------------------------------------------------------
# Installs the argostranslate models for one language pair, so --def-lang
# works without prompting mid-run. Pairs with no direct model pivot through
# English, which is two downloads -- argostranslate publishes 49 packages into
# English and 49 out of it, but almost none between two non-English languages.

translate-model: venv
	@if [ -z "$(LANGUAGE)" ] || [ -z "$(DEF_LANG)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  LANGUAGE and DEF_LANG are both required.\n"; \
		printf "  Usage: $(CYAN)make translate-model LANGUAGE=de DEF_LANG=en$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Installing translation for %s -> %s...\n" "$(LANGUAGE)" "$(DEF_LANG)"
	@PYTHONPATH=src $(VENV_PYTHON) -c \
		"from pipeline.translation import install_translation; \
		import sys; \
		sys.exit(0 if install_translation('$(LANGUAGE)', '$(DEF_LANG)') else 1)"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Translation ready: %s -> %s\n" "$(LANGUAGE)" "$(DEF_LANG)"

# -- spacy-model --------------------------------------------------------------
# Model name is resolved from SPACY_LANG via language.get_spacy_model() --
# not duplicated here -- so this can never drift from the mapping nlp.py
# actually uses. Matches the PYTHONPATH=src convention run/review/backlog
# already use below rather than depending on `install`, which would force
# a full pip reinstall pass on every invocation.

spacy-model: venv
	$(eval SPACY_MODEL_NAME := $(shell PYTHONPATH=src $(VENV_PYTHON) -c \
		"from pipeline.language import get_spacy_model, SpacyModelUnavailableError; \
		import sys; \
		sys.stdout.write(get_spacy_model('$(SPACY_LANG)'))" 2>/dev/null))
	@if [ -z "$(SPACY_MODEL_NAME)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  '$(SPACY_LANG)' isn't supported thus far.\n"; \
		printf "  Run $(CYAN)python -m pipeline --list-languages$(RESET) to see all languages,\n"; \
		printf "  or check language.SPACY_MODELS for spaCy-supported codes specifically.\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Downloading spaCy model: %s\n" "$(SPACY_MODEL_NAME)"
	@$(VENV_PYTHON) -m spacy download $(SPACY_MODEL_NAME) --quiet
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Downloading NLTK WordNet data...\n"
	@$(VENV_PYTHON) -m nltk.downloader wordnet omw-2.0 --quiet 2>/dev/null || true
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  spaCy model and NLTK data ready.\n"

# -- translate-setup ---------------------------------------------------------

translate-setup: venv
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Setting up translation dependencies...\n"
# torch is installed first, and from PyTorch's CPU index on purpose.
#
# argostranslate needs stanza, stanza needs torch, and stanza asks only for
# "torch>=1.3.0" with no variant. pip therefore takes the default PyPI wheel,
# which since torch 2.x bundles CUDA and drags in nvidia-* and triton:
# measured on this repo, 1.1 GB of torch plus 2.7 GB of nvidia plus 689 MB of
# triton, 4.5 GB in total, 76% of the whole virtualenv.
#
# None of it is usable without an NVIDIA GPU and a current driver, and it is
# dead weight on an AMD card, an integrated one, or a machine with none.
# Measured on the developer machine, which has an NVIDIA card:
# torch.cuda.is_available() returned False because the driver was too old, so
# 4.5 GB was installed and never once used.
#
# The CPU build is 733 MB installed and brings no nvidia or triton at all,
# taking the virtualenv from 5.9 GB to 2.2 GB. Installing it first means pip
# sees torch as already satisfied when argostranslate asks.
#
# Anyone who does have a working GPU can install the CUDA or ROCm build over
# the top afterwards; nothing here prevents that. See ARCHITECTURE.md 8.41.
#
# Two branches, because a machine that already has the CUDA build needs a
# different command from one that has no torch at all. Installing from the
# CPU index is enough for the second: pip sees the requirement satisfied
# when argostranslate asks. For the first it is a no-op: pip reports
# "already satisfied" and the 4.5 GB stays exactly where it was. That is
# the trap this target had until 26 August 2026: it read as the fix and
# changed nothing for everyone who had already run it once.
#
# Replacing an installed build needs --force-reinstall, and --no-deps with
# it because the CPU index is flat and a full resolve against it fails.
# Torch's other dependencies are already installed and are not touched.
# The orphans need a second command: pip never removes them, and torch
# alone leaves 3.4 GB of nvidia and triton with nothing able to call it.
#
# The condition is the same one --doctor reports, and deliberately not just
# "is this a CUDA build". A CUDA build on a machine with a working GPU is
# someone's deliberate choice, and this target must not undo it.
	@if $(VENV_PYTHON) -c "import torch, sys; sys.exit(0 if torch.version.cuda and not torch.cuda.is_available() else 1)" >/dev/null 2>&1; then \
		printf "$(YELLOW)$(BOLD)[warn]$(RESET)  A CUDA torch build is installed but no GPU here can use it. Replacing it...\n"; \
		$(VENV_PIP) install --quiet --no-deps --force-reinstall --index-url https://download.pytorch.org/whl/cpu torch; \
		$(VENV_PIP) uninstall -y -q triton $$($(VENV_PIP) list --format=freeze 2>/dev/null | grep -i "^nvidia" | cut -d= -f1 | tr "\n" " "); \
		printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  CPU-only torch installed, nvidia and triton removed (3.7 GB freed here).\n"; \
	else \
		printf "$(CYAN)$(BOLD)[info]$(RESET)  Installing CPU-only torch (the CUDA build is 4.5 GB and unusable without an NVIDIA GPU)...\n"; \
		$(VENV_PIP) install --quiet --index-url https://download.pytorch.org/whl/cpu torch; \
	fi
	@$(VENV_PIP) install --quiet argostranslate libretranslate
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  argostranslate and libretranslate installed.\n"
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Translation models will be downloaded on first use.\n"
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Language pair loaded from LANGUAGE and DEF_LANG in .env\n"
	@$(VENV_PYTHON) -c "\
from argostranslate import package as pkg; \
import os; \
from_code = os.getenv('LANGUAGE','en'); \
to_code   = os.getenv('DEF_LANG','en'); \
pkg.update_package_index(); \
available = pkg.get_available_packages(); \
match = [p for p in available if p.from_code==from_code and p.to_code==to_code]; \
print(f'  Found model: {from_code}->{to_code}') if match else print(f'  No model for {from_code}->{to_code}'); \
"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Translation setup complete. Models download on first use.\n"

# -- translate-stop -----------------------------------------------------------

translate-stop:
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Stopping local LibreTranslate server if running...\n"
	@pkill -f "libretranslate" 2>/dev/null && \
		printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  LibreTranslate stopped.\n" || \
		printf "$(YELLOW)$(BOLD)[warn]$(RESET)  LibreTranslate was not running.\n"

# -- test ---------------------------------------------------------------------

test: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running unit tests (no network, no Anki required)...\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pytest tests/ \
		-m "not integration" \
		--tb=short \
		-q
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Unit tests passed.\n"

# -- check --------------------------------------------------------------------
# The pre-commit gate (CLAUDE.md 10). Runs the unit suite and exits non-zero
# if anything fails, with no pipe to swallow the status.
#
# Deliberately NOT gated on lint or typecheck. Both are red on code that
# predates this target -- 176 ruff findings, 70 of them the house Optional[X]
# idiom, and 19 mypy errors -- so requiring them would block every commit
# rather than catch anything. They run here as advisory output, and the
# counts are printed so a change that makes either worse is visible. Tighten
# this to a hard gate once the existing debt is cleared.

check: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Pre-commit check: unit tests...\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pytest tests/ -m "not integration" --tb=short -q
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Advisory (not gating):\n"
	@printf "    ruff  "; $(VENV_PYTHON) -m ruff check src/pipeline/ tests/ 2>/dev/null \
		| tail -2 | head -1 || true
	@printf "    mypy  "; $(VENV_PYTHON) -m mypy src/pipeline/ --ignore-missing-imports 2>/dev/null \
		| tail -1 || true
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Check passed.\n"

# -- test-all -----------------------------------------------------------------

test-all: check-os
	@printf "$(YELLOW)$(BOLD)[warn]$(RESET)  Integration tests require network access and a running Anki instance.\n"
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running full test suite...\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pytest tests/ \
		--tb=short \
		-q
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Full test suite passed.\n"

# -- coverage -----------------------------------------------------------------

coverage: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running unit tests with coverage...\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pytest tests/ \
		-m "not integration" \
		--tb=short \
		-q \
		--cov=pipeline \
		--cov-report=term-missing
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Coverage report complete.\n"

# -- format -------------------------------------------------------------------

format: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Formatting source files with black...\n"
	@$(VENV_PYTHON) -m black src/pipeline/ tests/
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Formatting complete.\n"

# -- lint ---------------------------------------------------------------------

lint: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running ruff...\n"
	@$(VENV_PYTHON) -m ruff check src/pipeline/ tests/
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Lint passed.\n"

# -- typecheck ----------------------------------------------------------------

typecheck: check-os
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running mypy...\n"
	@$(VENV_PYTHON) -m mypy src/pipeline/ --ignore-missing-imports
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Type check passed.\n"

# -- run ----------------------------------------------------------------------

run: check-os
	@if [ -z "$(VIDEO_ID)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  VIDEO_ID is required.\n"; \
		printf "  Usage: $(CYAN)make run VIDEO_ID=<youtube_video_id> DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@if [ -z "$(DECK)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  DECK is required.\n"; \
		printf "  Usage: $(CYAN)make run VIDEO_ID=<youtube_video_id> DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running pipeline for video: %s\n" "$(VIDEO_ID)"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--video-id="$(VIDEO_ID)" \
		--deck="$(DECK)" \
		$(if $(LANGUAGE),--language="$(LANGUAGE)",) \
		$(if $(DEF_LANG),--def-lang="$(DEF_LANG)",) \
		$(if $(FORCE),--force,)

# -- review -------------------------------------------------------------------

review: check-os
	@if [ -z "$(DECK)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  DECK is required.\n"; \
		printf "  Usage: $(CYAN)make review DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Processing review file for deck: %s\n" "$(DECK)"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--review \
		--deck="$(DECK)" \
		$(if $(LANGUAGE),--language="$(LANGUAGE)",) \
		$(if $(DEF_LANG),--def-lang="$(DEF_LANG)",)

# -- backlog ------------------------------------------------------------------

backlog: check-os
	@if [ -z "$(DECK)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  DECK is required.\n"; \
		printf "  Usage: $(CYAN)make backlog DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Processing Anki backlog for deck: %s\n" "$(DECK)"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--process-backlog \
		--deck="$(DECK)" \
		$(if $(LANGUAGE),--language="$(LANGUAGE)",) \
		$(if $(DEF_LANG),--def-lang="$(DEF_LANG)",)

# -- clean --------------------------------------------------------------------

clean: check-os
	@printf "$(YELLOW)$(BOLD)[warn]$(RESET)  This will remove the virtual environment and all generated output.\n"
	@printf "  Continue? [y/N] "; \
	read confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		rm -rf $(VENV_DIR); \
		rm -rf output/; \
		rm -rf .mypy_cache/; \
		rm -rf .ruff_cache/; \
		rm -rf .pytest_cache/; \
		find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; \
		find . -name "*.pyc" -delete 2>/dev/null; \
		find . -name "*.pyo" -delete 2>/dev/null; \
		printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Clean complete.\n"; \
	else \
		printf "$(YELLOW)$(BOLD)[warn]$(RESET)  Clean cancelled.\n"; \
	fi

# -- help ---------------------------------------------------------------------

help:
	@printf "\n"
	@printf "$(BOLD)yt-anki-pipeline$(RESET) — YouTube to Anki flashcard pipeline\n"
	@printf "\n"
	@printf "$(BOLD)First-time setup:$(RESET)\n"
	@printf "  $(CYAN)make all$(RESET)                              Create venv, install deps, download spaCy model\n"
	@printf "  $(CYAN)make setup$(RESET)                            Guided .env setup for an optional MW API key\n"
	@printf "  $(CYAN)make dictionary$(RESET) LANGUAGE=<code>        Offline Wiktionary definitions (large one-time download)\n"
	@printf "\n"
	@printf "$(BOLD)Run the pipeline:$(RESET)\n"
	@printf "  $(CYAN)make run$(RESET) VIDEO_ID=<id> DECK=\"<name>\"   Process a YouTube video\n"
	@printf "        optional: LANGUAGE=fr  DEF_LANG=en  FORCE=1 (reprocess a done video)\n"
	@printf "  $(CYAN)make review$(RESET) DECK=\"<name>\"               Process deferred review.json words\n"
	@printf "  $(CYAN)make backlog$(RESET) DECK=\"<name>\"              Process Anki backlog (Anki must be running)\n"
	@printf "        both optional: LANGUAGE=fr  DEF_LANG=en\n"
	@printf "\n"
	@printf "  $(CYAN)make doctor$(RESET)                           What is installed, what is missing\n"
	@printf "\n"
	@printf "$(BOLD)Every target above has a CLI equivalent, for use without make:$(RESET)\n"
	@printf "  python -m pipeline --doctor\n"
	@printf "  python -m pipeline --setup\n"
	@printf "  python -m pipeline --list-languages\n"
	@printf "  python -m pipeline --install-model de\n"
	@printf "  python -m pipeline --install-translation de:en\n"
	@printf "  python -m pipeline --build-dictionary de\n"
	@printf "  python -m pipeline --video-id <id> --deck \"<name>\" --language de\n"
	@printf "  python -m pipeline --review --deck \"<name>\"\n"
	@printf "  python -m pipeline --process-backlog --deck \"<name>\"\n"
	@printf "\n"
	@printf "$(BOLD)Development:$(RESET)\n"
	@printf "  $(CYAN)make test$(RESET)                             Unit tests only (no network or Anki needed)\n"
	@printf "  $(CYAN)make test-all$(RESET)                         Full suite including integration tests\n"
	@printf "  $(CYAN)make coverage$(RESET)                         Unit tests plus a per-module coverage report\n"
	@printf "  $(CYAN)make format$(RESET)                           Auto-format with black\n"
	@printf "  $(CYAN)make lint$(RESET)                             Lint with ruff\n"
	@printf "  $(CYAN)make typecheck$(RESET)                        Type check with mypy\n"
	@printf "  $(CYAN)make spacy-model$(RESET)                      Re-download spaCy model separately\n"
	@printf "  $(CYAN)make translate-setup$(RESET)                 Install LibreTranslate for translation mode\n"
	@printf "  $(CYAN)make translate-stop$(RESET)                  Stop local LibreTranslate server\n"
	@printf "\n"
	@printf "$(BOLD)Maintenance:$(RESET)\n"
	@printf "  $(CYAN)make clean$(RESET)                            Remove venv, output, and cache files\n"
	@printf "\n"