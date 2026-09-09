# ADR-013: Test isolation is enforced, not asked for

Status: Accepted, 8 September 2026

Continues the numbering from ADR-012. Like that one, this comes out of the
September audit rather than from a feature.

## The rule that was not holding

CLAUDE.md 3.5 has said this since the file was written:

> No test in the default run may require network access, a running Anki
> instance, or an installed spaCy or translation model.

On 8 September 2026 the suite was run with `socket.socket.connect` patched
to raise, to find out whether that was true. **Thirteen tests opened
outbound connections**, twenty-four attempts in total: twelve in
`test_definition.py` reaching en.wiktionary.org and dictionaryapi.dev, and
one in `test_images.py` reaching Wikidata.

## Why the rule failed, which is the part worth recording

Not one of those tests was written badly. Every one of them mocked every
source the function under test had **at the time it was written**.

`find_image` is the clearest case. Its test mocked two network calls. That
morning, hours before the audit, the function grew a third call to resolve a
Wikidata description. The test kept passing, because a test that mocks two
of three sources still exercises the code, still asserts the right thing,
and quietly reaches the internet for the third.

So the failure mode is not carelessness. It is that **a correct test decays
when the code beneath it grows a dependency**, silently, with nothing to
notice. Reviewing harder does not fix that, because the review that would
catch it happens on the commit that changes the *source*, where no test file
appears in the diff at all.

The previous handover had found twelve of these by hand and recorded them as
"found and deliberately not fixed". The guard found thirteen. Counting by
hand missed one, which is its own argument.

## Options considered

**1. Keep it as prose and rely on review.** Rejected. It had been prose for
the whole life of the project and thirteen violations accumulated under it.
The one existing test that stood in for 3.5 checked that pytest markers were
spelled correctly, which is a different question entirely: it could not have
caught any of the thirteen.

**2. Ban the network libraries from the test environment.** Rejected as too
blunt. Integration tests exist and must reach real services, and the same
interpreter runs both.

**3. Fail any default-run test that opens a non-loopback connection.**
Chosen.

## The decision

An autouse fixture in `tests/conftest.py` patches the socket layer for every
test in the default run and raises, naming the test, if anything connects to
another machine.

Two carve-outs, both deliberate:

- **Loopback is allowed.** Standing up an `http.server` on a spare port is a
  technique this project uses to reproduce failures, and CLAUDE.md 18.7
  records it as one that has earned its place. Banning it would cost a real
  practice to prevent nothing.
- **`@pytest.mark.integration` is exempt**, because reaching a real service
  is the entire purpose of those tests.

## Consequences

- The constraint is now checked on every run rather than asserted in a
  document. The thirteen were mocked properly and the suite is offline.
- A test that decays the way `find_image`'s did now fails immediately, on
  the commit that adds the dependency, which is where the person who can fix
  it is already looking.
- CI stops being able to pass because a service happened to be up, and stops
  being able to fail because one was down.
- The rule is cheap to keep: the fixture is a few lines and needs no
  maintenance as sources are added.

## The general shape, which is the reusable part

A constraint written only as prose is a constraint that will be violated,
and the violation will be invisible because nothing is looking. When a rule
matters enough to write in CLAUDE.md, the question that follows is what
would fail if someone broke it. If the answer is "nothing", the rule is a
preference.

This is the same argument that put tests behind all six hard constraints in
`tests/test_hard_constraints.py`. ARCHITECTURE 8.49 records the measurement.
