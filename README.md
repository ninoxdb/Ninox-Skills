# Ninox Skills

Agent skills that teach AI assistants how to build on [Ninox](https://ninox.com), the low-code database platform. Each skill is a self-contained folder with a `SKILL.md` plus reference docs, scripts, and templates, following the [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) format. Drop them into a skills-capable assistant (Claude Code, Claude.ai, or anything else that reads the format) and it knows how to work with Ninox instead of guessing.

## What's in here

### `ninox`

The core skill: operating the Ninox 4 Public API (`go.ninox.com`) end to end.

- Schema discovery: workspaces, modules, tables, fields
- Record CRUD with filtering, plus CSV import (append / update / upsert)
- Schema administration: creating and modifying modules, tables, and fields through the API, including formula (`function`) fields with live Ninox script expressions
- Writing Ninox script and translating Excel formulas into it
- Credential handling via `NINOX_API_KEY` and an optional `~/.ninox/.env` for saved workspaces

The skill covers the Ninox 4 API generation (workspace → modules → tables), not the older `api.ninox.com` teams/databases API, and documents where the two differ so they don't get mixed up.

### `ninox-custom-widget`

Building Custom Widgets for the Ninox v4 PageBuilder: sandboxed HTML/CSS/JS mini-apps that live inside a module and talk to the host through the `window.Ninox` SDK.

- The four-file widget anatomy (`widget.json`, `index.html`, `script.js`, `style.css`) with a working template to copy
- The full bridge API: lifecycle, typed properties, and data access (`get`, `create`, `update`, `remove`)
- How widgets are stored, served, and authored in the in-app IDE
- Where Custom Widgets end and the separate Dynamic HTML component begins

### `excel-to-ninox`

A phased workflow for turning an Excel workbook into a Ninox app that replaces it, not a cell-by-cell copy. It transcribes the workbook's actual logic (formulas, running totals, lookup chains, scattered calculator blocks), gets the design approved by the user before anything is built, then constructs the app through the Public API. Includes an inspector script that handles very large workbooks without loading every cell, and hard rules against silent workarounds: a formula that won't deploy is reported as an error, never quietly replaced with static values.

Depends on the `ninox` skill for API mechanics.

## Installation

Copy the skill folders into wherever your assistant looks for skills. For Claude Code:

```bash
git clone https://github.com/<you>/<this-repo>.git
cp -r <this-repo>/ninox <this-repo>/ninox-custom-widget <this-repo>/excel-to-ninox ~/.claude/skills/
```

On Claude.ai, upload the folders as custom skills in your settings.

## Requirements

- A Ninox workspace on Ninox 4 with an API key (Workspace → Integrations)
- For the API skill: `curl` and `python3` on the machine the assistant runs on
- `NINOX_API_KEY` in the environment, or saved in `~/.ninox/.env`

Custom Widgets additionally sit behind Ninox's `CustomWidgets` feature flag.

## A note on trust

These skills tell an AI assistant how to read and write real data in your workspace. They're written to be careful by default — discovery before writes, verification reads after mutations, explicit confirmation before anything destructive — but review them yourself before pointing an assistant at a production workspace, and test against a scratch module first.

## License

MIT
