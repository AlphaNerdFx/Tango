---
name: commit
description: Verify then commit safely
---

1. Run `git status` and `git diff --stat`. Never discard uncommitted work.
2. Run `make check` as a BARE command (no pipes). Print the exit code. If non-zero, STOP and report.
3. For any test added in this change, mutate the target function and confirm the test fails; revert the mutation.
4. Stage only files relevant to this change (no `git add -A`).
5. Commit with a conventional-commit message summarizing the why, not the what.
