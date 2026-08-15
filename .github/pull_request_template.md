<!--
Thanks for contributing to Tango. CONTRIBUTING.md has the full guide; this
template is the short version of what a reviewer will look for.
-->

## What this changes, and why

<!-- The why, not the what — the diff already says what. -->

## Related issues

<!-- Closes #123 -->

## Checklist

- [ ] `make check` exits 0. Run it bare — piping to `tail` or `head` masks
      the exit code.
- [ ] New tests fail when the code under test is broken. Mutate the target
      (flip a condition, return a wrong constant), confirm the test fails,
      revert. A green suite is not evidence that anything is checked —
      six vacuous tests have been found in this suite.
- [ ] Any claim about behaviour was verified by running it, not inferred
      from the code. Paste real output for anything measured.
- [ ] Docs updated where the change makes them wrong — `ARCHITECTURE.md` for
      a design decision, `CHANGELOG.md` under `[Unreleased]`, `CLAUDE.md` if
      a constraint moved.

## Hard constraints

`CLAUDE.md` §3 lists six constraints that are enforced by
`tests/test_hard_constraints.py`. If this PR touches any of them, say which
and why:

- [ ] Does not change `ANKI_MODEL_ID` or `ANKI_DECK_ID` (§3.1)
- [ ] Card fields appended, never reordered or inserted (§3.2)
- [ ] Examples, synonyms and antonyms stay in the transcript language (§3.3)
- [ ] Validates `token.lemma_`, not `token.text` (§3.4)
- [ ] No test in the default run needs network, Anki, or an installed model (§3.5)
- [ ] No heavy dependency added to the base install (§3.6)

## Migration

<!--
Does an existing user have to DO something, or does something they already
have change shape? If yes this is at least a MINOR version bump, and the
migration belongs in CHANGELOG.md. Adding a field to the Anki notetype is
the canonical example — see v0.5.0.
-->

- [ ] No migration required, **or** the migration is described above and in
      `CHANGELOG.md`
