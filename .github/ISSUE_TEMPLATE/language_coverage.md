---
name: Language coverage issue
about: A language is not working correctly for subtitle selection, definitions, or translation
title: "[LANG] "
labels: language
assignees: ''
---

## Language affected

Language name and BCP-47 code (e.g. French, fr):

## What is not working

- [ ] Subtitle selection fails or picks the wrong language
- [ ] Deck name not recognised as this language
- [ ] Native definitions not returned by dictionaryapi.dev
- [ ] Translation not available for this language pair
- [ ] Example sentences are in the wrong language
- [ ] Synonyms or antonyms missing

## Steps to reproduce

Video ID used (a public video with captions in this language):

Command run:

```bash
make run VIDEO_ID= DECK= LANGUAGE=
```

Error or unexpected output:

```
paste here
```

## Expected behaviour

Describe what you expected to happen.