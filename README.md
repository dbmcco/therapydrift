# therapydrift

`therapydrift` is a Speedrift-suite sidecar for **self-healing drift loops**.

It detects repeated drift signals on a task (for example repeated `Coredrift:` logs) and unresolved drift follow-up tasks, then creates a deterministic recovery task (`drift-therapy-<task>`) when requested.

## Ecosystem Map

This project is part of the Speedrift suite for Workgraph-first drift control.

- Spine: [Workgraph](https://graphwork.github.io/)
- Orchestrator: [driftdriver](https://github.com/dbmcco/driftdriver)
- Baseline lane: [coredrift](https://github.com/dbmcco/coredrift)
- Optional lanes: [specdrift](https://github.com/dbmcco/specdrift), [datadrift](https://github.com/dbmcco/datadrift), [depsdrift](https://github.com/dbmcco/depsdrift), [uxdrift](https://github.com/dbmcco/uxdrift), [therapydrift](https://github.com/dbmcco/therapydrift), [yagnidrift](https://github.com/dbmcco/yagnidrift), [redrift](https://github.com/dbmcco/redrift)

## Task Spec Format

Add a per-task fenced TOML block:

````md
```therapydrift
schema = 1
min_signal_count = 2
followup_prefixes = ["drift-", "coredrift-pit-"]
require_recovery_plan = true
ignore_signal_prefixes = ["Therapydrift:"]
cooldown_seconds = 1800
max_auto_actions_per_hour = 2
min_new_signals = 1
circuit_breaker_after = 6
```
````

Loop-safety defaults:
- deterministic follow-up id (`drift-therapy-<task>`) for idempotency
- cooldown + hourly budget + circuit breaker to prevent churn
- requires new evidence (`min_new_signals` or follow-up set change) before auto action
- self-signal filtering (`ignore_signal_prefixes`) to avoid recursive triggers

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

## Agent Guidance

This section is for AI agents (Claude Code, Codex, Amplifier) working in Speedrift-managed repos.

### When This Lane Runs

`therapydrift` runs automatically when repeated drift signals accumulate on a task. It is also triggered by `driftdriver` during factory cycles and attractor loop passes.

### Per-Task Workflow

1. therapydrift detects recurring drift patterns and creates deterministic recovery tasks (`drift-therapy-<task>`)
2. Run drift checks at task start and before completion:
   ```bash
   ./.workgraph/drifts check --task <id> --write-log --create-followups
   ```
3. Drift is advisory — never hard-block the current task
4. If findings appear, prefer follow-up tasks over scope expansion

### Key Rules

- Exit code `0` = clean, `3` = findings exist (advisory)
- Follow-up tasks are deduped and capped at 3 per lane per repo
- Do not suppress findings — let driftdriver manage significance scoring
- Do NOT create manual therapy tasks — let therapydrift detect patterns and create them
- The lane has built-in loop safety: cooldown, hourly budget, circuit breaker
- Deterministic follow-up IDs prevent duplicate recovery tasks
- Self-signal filtering prevents therapydrift from triggering itself
