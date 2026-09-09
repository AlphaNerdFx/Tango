# ADR-012: A setting means what it says, or it is not a setting

Status: Accepted, 9 September 2026

Continues the numbering from ADR-011. This one comes out of the September
code audit rather than from a feature, and it settles a rule that was
already half-decided in the codebase without anyone writing it down.

## The problem, and how it was found

`.env.example` is the file the setup instructions tell a user to copy. Ten
of its keys ship with nothing on the right-hand side, which is the ordinary
way to write "here is the name, fill it in if you want it".

python-dotenv does not read it that way. `KEY=` puts `KEY` into the
environment as an empty string, so `os.getenv("KEY", default)` finds the
variable, returns `""`, and never reaches the default. That is the opposite
of what the person editing the file intended.

It failed in two different ways, and the second is the serious one:

| kind of setting | what a blank value did |
|---|---|
| text, e.g. `WIKTIONARY_USER_AGENT` | silently replaced a working default with `""` |
| numeric, e.g. `API_TIMEOUT` | `float("")` raised `ValueError` |

The numeric case is not a bad value in a corner of the program. `config` is
imported when `pipeline.__main__` is imported, which happens before `main()`
exists to catch anything, so the user gets a raw traceback before the
program starts. CLAUDE.md 4.4 says the pipeline must not produce a traceback
for any expected failure, and "someone left a line blank in a config file"
is about as expected as failures get.

Reproduced before it was fixed, per CLAUDE.md 18.7:

```
$ API_TIMEOUT= tango doctor
ValueError: could not convert string to float: ''
```

**The knowledge already existed in the repository and had been applied to
exactly one of the three categories.** `_resolve_path` handled it, its
docstring said "unset or empty", and `test_empty_env_value_falls_back_to_
default` in `tests/test_config.py` spells the whole mechanism out, including
that `.env.example` ships keys with no value. Paths were safe. The numeric
and text settings, written by the same hands from the same understanding,
were not. Nothing connected them, because the rule lived in one function's
docstring rather than anywhere a reader would look for a project-wide rule.

## Options considered

**1. Leave it, and tell users not to write blank lines.** Rejected. The file
that teaches the blank lines is the file this project ships and tells people
to copy. Documentation cannot fix a footgun that the documentation is
handing over.

**2. Strip blank keys out of `.env.example`.** Rejected, and it was the
first instinct. It narrows the template to only what a user "needs", which
was the original request, but it loses the documentation value of listing a
setting that exists, and `test_the_example_file_documents_every_live_setting`
deliberately requires every live setting to appear. It also fixes nothing
for anyone who writes a blank line themselves, which is the actual failure.

**3. Raise a typed error naming the setting.** Rejected on the import-order
fact above. `main()` catches `TangoError`, but nothing catches an exception
thrown while `config` is being imported, so a raise here is a traceback
wearing a better message. Making it catchable would mean deferring every
setting's resolution behind a function call, which is a large change to
every module for a small problem.

**4. Treat blank as unset everywhere, and report a malformed value rather
than raising.** Chosen.

## The decision

A blank value means unset, for every setting, everywhere. `_env`,
`_env_int` and `_env_float` in `config.py` implement it, and
`_resolve_path` already did. Whitespace counts as blank, and a value is
stripped before use, so `IMAGES_ENABLED= true` now does what it says instead
of being silently ignored.

A value that is present but unparseable is a different case and is not
treated as blank. Silently using the default there would hide a real typo.
It cannot raise, for the reason in option 3, so it is recorded in
`MALFORMED_ENV_VALUES` and `tango doctor` prints the name, the value the
user wrote, and the fact that the default was used instead.

This extends the convention `_resolve_path` already had rather than
inventing a second one. That was the deciding argument: the project did not
need a new rule, it needed the rule it already had to apply to the other
two thirds of its settings.

## Consequences

- Ten keys that ship blank in `.env.example` are now safe to leave exactly
  as they are, and the file says so.
- A blank numeric setting no longer stops the program.
- `WIKTIONARY_USER_AGENT` cannot be silently emptied, which matters because
  Wikimedia's User-Agent policy rejects the empty agent and the failure
  would have looked like the dictionary being down.
- `tango doctor` gains one more thing it can tell you, alongside the
  settings that nothing reads.
- One behaviour changed rather than being preserved: a padded value like
  `  true  ` is now honoured. Checked case by case before the change went
  in; every other input, including unset, blank, `true`, `false`, `1`, `0`
  and nonsense, resolves exactly as it did.

## The same audit, the same category: a setting that did nothing

`LIBRETRANSLATE_URL` was documented in two places. `.env.example` called it
"Local LibreTranslate server URL, used as a fallback if community mirrors
are unavailable", and the comment above it in `translation.py` said "Set
LIBRETRANSLATE_URL to your own instance and it is used first".

Neither was true. The value was read into `LIBRETRANSLATE_LOCAL` and that
constant appeared exactly once in the whole repository, on the line that
defined it. Traced through `git log -S`: it has been dead since the commit
that introduced it on 10 July 2026. Setting it has never done anything.

It was found by asking a question the other checks did not: for every
setting the code reads, is the constant it fills used anywhere? Thirty-four
settings, one answer of zero.

That is a worse failure than a stray key. `tango doctor` reports a name
nothing reads, and this name *was* read, so doctor called it healthy. A
setting that resolves and is then ignored looks correct from every angle
except the one that matters.

**Decision: removed, not wired up.** Three options were weighed, and the
deciding fact is that a local server already works today by going in
`LIBRETRANSLATE_MIRRORS`, which is consumed. Wiring `LIBRETRANSLATE_URL` up
would have added a second way to say the same thing, and adding a working
feature is not what a documentation audit is for. Leaving it and only
correcting the prose would have kept a setting that looks real and is not.

Anyone who has the key in their own `.env` now gets told by `tango doctor`
that it has no effect, which is exactly right and is more than they got
before.

## What this does not cover

Two settings are read at their use site rather than through `config`:
`ARGOS_PACKAGES_DIR` and `TANGO_DEBUG`. Both are consumed as plain truthy
strings where blank and unset already mean the same thing, so neither is
affected. If either ever grows a non-empty default, it belongs behind
`_env` like the rest.
