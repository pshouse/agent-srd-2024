# Issue triage agent

An automated triage agent runs hourly on weekdays. It is in a triage-only trial until Dec 3, 2026, and it never writes to GitHub during the trial.

Building starts only when the maintainer assigns an issue or applies `agent:approved`.

`agent:stop` on an issue, or a repo-level stop, halts the agent.

Agent pull requests go through CTO review and maintainer merge. Nothing goes to `main` directly.

Issue text is treated as untrusted input.
