# =============================================================================
# yt-anki-pipeline — Makefile
# =============================================================================
# Targets:
#   make all          — full first-time setup (venv + install + spaCy model)
#   make venv         — create virtual environment
#   make install      — install package and all dependencies into venv
#   make spacy-model  — download en_core_web_sm separately
#   make test         — run unit tests only (no network, no Anki required)
#   make test-all     — run full suite including integration tests
#   make format       — auto-format source and test files with black
#   make lint         — check code style with ruff
#   make typecheck    — static type checking with mypy
#   make run          — run the pipeline (VIDEO_ID and DECK required)
#   make review       — process the review.json file
#   make backlog      — process the Anki backlog for a deck
#   make clean        — remove venv, output, cache files
#   make check-os     — warn if running on Windows without a compatible shell
# =============================================================================

# -- Configuration ------------------------------------------------------------

PYTHON        := python3
VENV_DIR      := .tangovenv
VENV_PYTHON   := $(VENV_DIR)/bin/python
VENV_PIP      := $(VENV_DIR)/bin/pip
VENV_ACTIVATE := $(VENV_DIR)/bin/activate

SPACY_MODEL   := en_core_web_sm
MIN_PYTHON    := 3.9

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

.PHONY: all venv install spacy-model translate-setup translate-stop \
        test test-all format lint typecheck \
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
		exit(0) if (v.major,v.minor)>=(3,9) \
		else print('Python $(MIN_PYTHON)+ required, found '+str(v.major)+'.'+str(v.minor)) or exit(1)"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Python version check passed.\n"

# -- install ------------------------------------------------------------------

install: venv
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Installing dependencies...\n"
	@$(VENV_PIP) install --quiet --upgrade pip
	@$(VENV_PIP) install --quiet -e ".[dev]"
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Dependencies installed.\n"

# -- spacy-model --------------------------------------------------------------

spacy-model: venv
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Downloading spaCy model: $(SPACY_MODEL)\n"
	@$(VENV_PYTHON) -m spacy download $(SPACY_MODEL) --quiet
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  spaCy model ready: $(SPACY_MODEL)\n"

# -- translate-setup ---------------------------------------------------------

translate-setup: venv
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Setting up LibreTranslate for translation mode...\n"
	@$(VENV_PIP) install --quiet libretranslate
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

# -- test-all -----------------------------------------------------------------

test-all: check-os
	@printf "$(YELLOW)$(BOLD)[warn]$(RESET)  Integration tests require network access and a running Anki instance.\n"
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running full test suite...\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pytest tests/ \
		--tb=short \
		-q
	@printf "$(GREEN)$(BOLD)[ ok ]$(RESET)  Full test suite passed.\n"

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
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Running pipeline for video: $(VIDEO_ID)\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--video-id "$(VIDEO_ID)" \
		--deck "$(DECK)" \
		$(if $(LANGUAGE),--language "$(LANGUAGE)",) \
		$(if $(DEF_LANG),--def-lang "$(DEF_LANG)",)

# -- review -------------------------------------------------------------------

review: check-os
	@if [ -z "$(DECK)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  DECK is required.\n"; \
		printf "  Usage: $(CYAN)make review DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Processing review file for deck: $(DECK)\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--review \
		--deck "$(DECK)"

# -- backlog ------------------------------------------------------------------

backlog: check-os
	@if [ -z "$(DECK)" ]; then \
		printf "$(RED)$(BOLD)[err ]$(RESET)  DECK is required.\n"; \
		printf "  Usage: $(CYAN)make backlog DECK=\"<deck name>\"$(RESET)\n"; \
		exit 1; \
	fi
	@printf "$(CYAN)$(BOLD)[info]$(RESET)  Processing Anki backlog for deck: $(DECK)\n"
	@PYTHONPATH=src $(VENV_PYTHON) -m pipeline \
		--process-backlog \
		--deck "$(DECK)"

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
	@printf "\n"
	@printf "$(BOLD)Run the pipeline:$(RESET)\n"
	@printf "  $(CYAN)make run$(RESET) VIDEO_ID=<id> DECK=\"<name>\"   Process a YouTube video\n"
	@printf "  $(CYAN)make review$(RESET) DECK=\"<name>\"               Process deferred review.json words\n"
	@printf "  $(CYAN)make backlog$(RESET) DECK=\"<name>\"              Process Anki backlog (Anki must be running)\n"
	@printf "\n"
	@printf "$(BOLD)Development:$(RESET)\n"
	@printf "  $(CYAN)make test$(RESET)                             Unit tests only (no network or Anki needed)\n"
	@printf "  $(CYAN)make test-all$(RESET)                         Full suite including integration tests\n"
	@printf "  $(CYAN)make format$(RESET)                           Auto-format with black\n"
	@printf "  $(CYAN)make lint$(RESET)                             Lint with ruff\n"
	@printf "  $(CYAN)make typecheck$(RESET)                        Type check with mypy\n"
	@printf "  $(CYAN)make spacy-model$(RESET)                      Re-download spaCy model separately\n	@printf "  $(CYAN)make translate-setup$(RESET)                 Install LibreTranslate for translation mode\n"
	@printf "  $(CYAN)make translate-stop$(RESET)                  Stop local LibreTranslate server\n""
	@printf "\n"
	@printf "$(BOLD)Maintenance:$(RESET)\n"
	@printf "  $(CYAN)make clean$(RESET)                            Remove venv, output, and cache files\n"
	@printf "\n"