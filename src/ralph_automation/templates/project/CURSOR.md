# Using this repo with Cursor

Before working in this repository, read [`AGENTS.md`](AGENTS.md). It is the shared collaboration protocol for Cursor, Claude, Gemini, Codex, and other agents.
If Cursor-specific guidance conflicts with `AGENTS.md` or the latest `agents/lead_engineer/CYCLE-*.md`, follow the shared protocol and latest cycle record.

This project can be used from Cursor through the shared `AGENTS.md` protocol. If a `.cursor/rules/` project rule is added later, it must point back to `AGENTS.md` instead of becoming a separate source of truth.

## In this repository

1. Open the folder in Cursor.
2. Read [`AGENTS.md`](AGENTS.md) first.
3. Then read the latest `agents/lead_engineer/CYCLE-*.md` and the relevant `TASK-*.md`.

## Use the same guidelines in another project

**Cursor (recommended):** Copy `AGENTS.md` into that project, or create a `.cursor/rules/` file that instructs Cursor to read `AGENTS.md` first. Adjust or merge with existing rules as you like.

**Other tools:** If a stack only supports a root instruction file, copy [`CLAUDE.md`](CLAUDE.md) into that project instead (or merge its contents into your existing instructions).

## Optional: personal Agent Skills

If you want the same content as a reusable skill under `~/.cursor/skills`, base that skill on [`AGENTS.md`](AGENTS.md). Keep the skill short and make it point back to the repository file.

## Claude Code vs Cursor

- **Claude Code:** Install via the plugin marketplace and [`README.md`](README.md) instructions; the plugin exposes the skill from this repo. Per-project use can also rely on `CLAUDE.md`.
- **Cursor:** Use `AGENTS.md` as the common project context. Cursor does not read `.claude-plugin/` or `CLAUDE.md` by default.

## For contributors

When you change shared agent behavior, update **[`AGENTS.md`](AGENTS.md)** first, then keep **[`CLAUDE.md`](CLAUDE.md)**, **[`GEMINI.md`](GEMINI.md)**, and any future Cursor rule in sync by pointing them back to `AGENTS.md`.
