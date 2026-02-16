# therapydrift

`therapydrift` is a Speedrift-suite sidecar for **self-healing drift loops**.

It detects repeated drift signals on a task (for example repeated `Speedrift:` logs) and unresolved drift follow-up tasks, then creates a deterministic recovery task (`drift-therapy-<task>`) when requested.

## Task Spec Format

Add a per-task fenced TOML block:

````md
```therapydrift
schema = 1
min_signal_count = 2
followup_prefixes = ["drift-", "speedrift-pit-"]
require_recovery_plan = true
```
````

## Workgraph Integration

From a Workgraph repo (where `driftdriver install` has written wrappers):

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

Standalone:

```bash
/path/to/therapydrift/bin/therapydrift --dir . wg check --task <id> --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory)
