# Backlog

One line per item, newest first. Machine grammar is enforced by `rigops backlog lint`.

Grammar:

open: `- [ ] YYYY-MM-DD area: symptom. Evidence: where seen. Fix: intended change.`
done: `- [x] YYYY-MM-DD area: symptom. Done YYYY-MM-DD: what changed.`

Default areas: hooks, skills, memory, permissions, metrics, repos, docs, jobs.

## Open

- [ ] 2026-08-01 hooks: a hook misfires on empty input. Evidence: stderr trace in the job log. Fix: add an empty-input guard before dispatch.
- [ ] 2026-07-15 memory: a store watermark tripped past its cap. Evidence: watermark check flagged the directory. Fix: run a compaction pass and re-check the cap.

## Done

- [x] 2026-06-01 docs: doc example was stale against the current grammar. Done 2026-06-02: rewrote the section to match the current field names.
