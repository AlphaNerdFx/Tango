"""
The compatibility document is the fixture, not prose.

`docs/COMPATIBILITY.md` states what v1.0.0 freezes. Every table in it is
read here and compared against the running code, **in both directions**:
what the document promises must exist, and what exists must be promised.

One direction alone is not enough, and this project has the scar. The
existing command test asserted `expected <= listed`, a subset, so `uninstall`
and `repair-images` shipped without ever being frozen and the freeze list in
ROADMAP still said ten commands while the CLI had twelve. A subset check
notices a broken promise and never notices an unmade one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer

import pipeline.__main__ as main_module
from pipeline.cards import FIELDS
from pipeline.config import DECK_ID, KNOWN_ENV_KEYS, MODEL_ID

DOC = Path(__file__).resolve().parent.parent / "docs" / "COMPATIBILITY.md"


def doc_text() -> str:
    """The document, for tests that are not using the fixture."""
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    """Return the body of one '## ' section, up to the next one."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)",
                      doc, re.S | re.M)
    assert match, f"COMPATIBILITY.md has no section '{heading}'"
    return match.group(1)


class TestTheNotetype:

    def test_the_ids_are_the_ones_the_document_promises(self, doc):
        body = _section(doc, "1. The notetype")
        stated = dict(re.findall(r"\| (MODEL_ID|DECK_ID) \| (\d+) \|", body))
        assert stated == {"MODEL_ID": str(MODEL_ID), "DECK_ID": str(DECK_ID)}

    def test_the_field_count_matches(self, doc):
        body = _section(doc, "1. The notetype")
        stated = int(re.search(r"\| field count \| (\d+) \|", body).group(1))
        assert stated == len(FIELDS)

    def test_every_field_is_named_at_its_index(self, doc):
        # The two-column table means a naive line regex reads across the
        # column boundary, which is exactly how the CLAUDE.md copy of this
        # table went stale unnoticed.
        body = _section(doc, "1. The notetype")
        stated = {}
        for row in body.splitlines():
            if not row.startswith("|") or set(row) <= set("|- "):
                continue
            cells = [c.strip() for c in row.strip("|").split("|")]
            for i in range(0, len(cells) - 1, 2):
                if cells[i].isdigit():
                    stated[int(cells[i])] = cells[i + 1]
        assert stated == dict(enumerate(FIELDS))

    def test_word_is_still_index_zero(self):
        # Anki treats the first field as the note's identity.
        assert FIELDS[0] == "Word"


class TestTheCommandSurface:

    @staticmethod
    def _live_commands() -> set[str]:
        return {
            c.name or c.callback.__name__.replace("_", "-")
            for c in main_module.app.registered_commands
        }

    def test_the_document_and_the_cli_name_the_same_commands(self, doc):
        body = _section(doc, "2. Commands")
        stated = set(re.findall(r"`([a-z][a-z-]+)`", body))
        live = self._live_commands()
        assert stated == live, (
            f"only in the document: {sorted(stated - live)}\n"
            f"only in the CLI (shipped without being frozen): "
            f"{sorted(live - stated)}")

    def test_the_stated_count_matches(self, doc):
        body = _section(doc, "2. Commands")
        stated = re.search(r"^(\w+), and their names are frozen", body, re.M)
        assert stated, "the count sentence is gone"
        words = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
                 "fourteen": 14}
        assert words[stated.group(1).lower()] == len(self._live_commands())


class TestTheOptionSurface:

    @staticmethod
    def _live_options(name: str) -> set[str]:
        cli = typer.main.get_command(main_module.app)
        cmd = cli.get_command(None, name)
        # secondary_opts holds the off-switch of a boolean pair. Without it
        # `--no-images` looks undocumented while being a real, frozen flag.
        return {o for p in cmd.params
                for o in list(p.opts) + list(p.secondary_opts)
                if o.startswith("--")}

    def test_every_command_in_the_table_has_exactly_those_options(self, doc):
        body = _section(doc, "3. Options")
        live_names = TestTheCommandSurface._live_commands()
        rows = [(n, c) for n, c in
                re.findall(r"^\| ([a-z][a-z-]+) \| (.+?) \|$", body, re.M)
                if n in live_names]          # skips the "| command |" header
        assert rows, "the option table is gone"

        wrong = []
        for name, cell in rows:
            stated = set()
            for opt in re.findall(r"`([^`]+)`", cell):
                # "--images/--no-images" is one option with two spellings.
                stated |= {part for part in opt.split("/")}
            live = self._live_options(name)
            if stated != live:
                wrong.append(f"{name}: document {sorted(stated)} vs "
                             f"cli {sorted(live)}")
        assert not wrong, "\n  ".join([""] + wrong)

    def test_a_command_with_options_is_not_missing_from_the_table(self, doc):
        # The unmade-promise direction. A new command with options must be
        # added to the table, not silently left out of it.
        body = _section(doc, "3. Options")
        tabled = {n for n in re.findall(r"^\| ([a-z][a-z-]+) \| ", body, re.M)
                  if n != "command"}
        cli = typer.main.get_command(main_module.app)
        missing = []
        for name in TestTheCommandSurface._live_commands():
            opts = self._live_options(name)
            # --help is on everything and is frozen at the top level.
            if opts - {"--help"} and name not in tabled:
                missing.append(f"{name}: {sorted(opts - {'--help'})}")
        assert not missing, (
            "These take options that nothing freezes:\n  " + "\n  ".join(missing))

    def test_the_top_level_options_are_the_ones_frozen(self):
        """
        The option table covers subcommands; this covers `tango` itself.

        Nothing checked the top-level options until 11 September 2026, so
        enabling shell completion added two to the public surface and every
        compatibility test still passed. That is the unmade-promise
        direction, on the one command every user types.
        """
        body = _section(doc_text(), "3. Options")
        stated = set(re.findall(r"`(--[a-z-]+)`", body.split("Frozen on the top-level")[1]))

        cli = typer.main.get_command(main_module.app)
        live = {o for p in cli.params for o in list(p.opts) + list(p.secondary_opts)
                if o.startswith("--")}
        # Click supplies --help through context_settings rather than as a
        # declared parameter, so collecting params alone misses it and the
        # document looks wrong when it is right.
        live |= {o for o in main_module.app.info.context_settings["help_option_names"]
                 if o.startswith("--")}

        assert stated == live, (
            f"only in the document: {sorted(stated - live)}\n"
            f"only on the command line: {sorted(live - stated)}")

    def test_help_has_its_short_alias(self):
        assert "-h" in main_module.app.info.context_settings["help_option_names"]


class TestExitCodes:

    def test_the_degraded_code_is_the_one_documented(self, doc):
        body = _section(doc, "4. Exit codes")
        stated = int(re.search(
            r"\| (\d) \| a package was written, but not one card", body).group(1))
        assert stated == main_module._EXIT_DEGRADED

    def test_the_documented_codes_are_the_ones_the_cli_uses(self, doc):
        # 0, 1 and 2 are conventions rather than constants, so this pins that
        # the document still claims all four and none has been quietly
        # dropped or renumbered.
        body = _section(doc, "4. Exit codes")
        codes = {int(c) for c in re.findall(r"^\| (\d) \| ", body, re.M)}
        assert codes == {0, 1, 2, 3}


class TestConfigurationKeys:

    def test_the_stated_count_matches_the_code(self, doc):
        body = _section(doc, "5. Configuration keys")
        stated = int(re.search(r"frozen, (\d+) of them", body).group(1))
        assert stated == len(KNOWN_ENV_KEYS)

    def test_the_two_flags_it_denies_are_still_not_keys(self):
        # The document says these are options, not settings. They were wrong
        # in ROADMAP and in the wiki, so this pins the denial.
        assert "DEF_LANG" not in KNOWN_ENV_KEYS
        assert "LANGUAGE" not in KNOWN_ENV_KEYS


class TestOnDiskSchemas:

    def test_the_index_versions_are_the_ones_documented(self, doc):
        from pipeline import antonyms, wiktdata

        body = _section(doc, "6. On-disk schemas")
        stated = dict(re.findall(r"\| (dictionary index|antonym index) \| (\d+) \|",
                                 body))
        assert stated == {
            "dictionary index": str(wiktdata._SCHEMA_VERSION),
            "antonym index": str(antonyms._SCHEMA_VERSION),
        }, "a schema bump costs every user a re-download, so it is a release decision"


class TestOutput:

    def test_the_filename_pattern_is_the_one_documented(self, doc, tmp_path,
                                                        monkeypatch):
        import pipeline.cards as cards_module

        body = _section(doc, "7. Output")
        assert "{video_id}_{YYYYMMDD_HHMMSS}.apkg" in body

        monkeypatch.setattr(cards_module, "OUTPUT_DIR", tmp_path)
        path = cards_module._build_output_path("abc123")
        assert re.fullmatch(r"abc123_\d{8}_\d{6}\.apkg", path.name), path.name
        assert path.parent == tmp_path
