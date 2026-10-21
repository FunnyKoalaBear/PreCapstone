---
name: next-feature
description: Build the next feature(s) from docs/queue.md, test, and commit each one
disable-model-invocation: true
argument-hint: [how many features, default 1]
allowed-tools: Bash(git add *) Bash(git commit *) Bash(git status *) Bash(git diff *) Bash(git log *) Bash(python manage.py *) Bash(py manage.py *)
---

Build $ARGUMENTS feature(s) (1 if empty) from docs/queue.md, top to bottom. Skip items marked [!].

For each feature:
1. Read its spec in docs/queue.md and the files it touches. If the spec leaves a real decision open, stop and ask; do not guess.
2. Implement it following CLAUDE.md. Short code, one-line comments at most.
3. Every new user-visible string gets a Japanese translation: makemessages -l ja, fill the .po, compilemessages. Check the Japanese reads naturally.
4. Add or update tests, then run `python manage.py check` and `python manage.py test`. Fix failures; after two failed attempts mark the item [!] with the reason and stop.
5. Delete the finished entry from docs/queue.md and include that deletion in the same commit as the feature. Stage only files under Web_Application/ and commit. Read `git log --oneline -15` first and match the message style. One commit per feature (migrations included).
6. Never push, amend, rebase or reset.

When done, report per feature: commit hash, one line on what changed, and 3 quick manual test steps.
