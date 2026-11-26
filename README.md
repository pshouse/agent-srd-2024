# Agent-Native 5e 2024 SRD Pack

A zero-server, local-first **D&D 5e 2024 (v5.2) SRD** knowledge pack compiled for AI agents and static web frontends. Built from the upstream CC-BY-4.0 dataset [`5e-bits/5e-srd-api`](https://github.com/5e-bits/5e-srd-api) (`packages/5e-database/src/2024/en`), replacing Web 2.0 server infrastructure (MongoDB, Express, GraphQL) with fast, deterministic local artifacts.

## What's Inside (Planned & In Progress)

- **Zero-Server SQLite + FTS5 + Cross-Reference Graph (`dist/srd2024.sqlite`)**: Self-contained database with normalized 2024 SRD entities, FTS5 full-text search, directed cross-reference edges, and CC-BY-4.0 provenance metadata.
- **Resolved-Bundle Query Engine**: Single-call entity resolution that bundles an entity with its depth-1 (configurable) referenced rules (conditions, weapon masteries, damage types, skills, etc.) under a token budget—eliminating N+1 lookup chatter.
- **Token-Tight Markdown Cards (`dist/cards/`)**: Compiled Markdown + YAML frontmatter cards with inline cross-reference awareness for progressive disclosure.
- **Agent Surfaces**:
  - Lightweight stdio **MCP server**
  - Fast **CLI** with matching query, bundle, filter, and graph commands
  - Drop-in **Agent Skill** (`skill/SKILL.md`)
- **Upcoming Static Frontend**: Zero-server-friendly static manifest/data exports supporting an interactive 5e 2024 SRD explorer, cross-reference graph visualizer, and resolved-bundle inspector.

## Attribution & License

SRD 5.2 content is sourced from [`5e-bits/5e-srd-api`](https://github.com/5e-bits/5e-srd-api) (`2024/en`) and includes material taken from the *System Reference Document 5.2* by Wizards of the Coast LLC, available under the [Creative Commons Attribution 4.0 International License (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/legalcode).

> *This is an initial bootstrap README and will be expanded with usage, CLI/MCP docs, and build instructions once the compiler and agent surfaces land.*
