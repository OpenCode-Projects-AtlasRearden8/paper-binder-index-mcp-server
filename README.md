# Paper Binder Index

A small, offline MCP server that indexes where your **physical** paperwork
lives — the deed in the fireproof binder under "Housing", the car title in
the fire safe, the passport in the drawer — so an AI agent can answer the
question that matters most in an emergency: **"where do I find...?"**

Paper documents are the ones digital organizers forget. This server is the
map from a document *name* to a document *place*: binder or drawer, tab,
and where any backup copies are kept. It also flags originals with no
backup, tracks a yearly refresh (great for reviewing passports and
policies once a year), and prints a table of contents you can slip into
the front of a real binder.

Built by [Asdale Dynamics, LLC](https://dylanasdale.github.io) — plain
English, local-first, no accounts, no cloud, no secrets.

## How it works

- **stdio MCP server**: speaks JSON-RPC 2.0 over stdin/stdout, so any MCP
  client (Claude Code / Desktop, opencode, others) can launch it.
- **Python standard library only.** Nothing to install; works offline.
- **One JSON file** (`paper_binder.json`) holds the whole index.
- **Auto-backup**: every write first makes a dated backup plus one rolling
  `.bak`, so a bad save is always one undo away.
- **Path safety**: the store resolves every candidate path and refuses
  anything outside its own data directory — no `../`, no absolute paths,
  no symlink escapes.

## Quick start

```bash
# run the offline unit tests (53 of them)
python3 -m unittest discover -s tests

# talk to the server for real: see the smoke-test guide below
bash smoke_test.sh
```

On first run the server seeds its data directory with a **sample binder**
(`sample_data/sample_binder.json`) so there is something to look at
immediately. Use `--no-sample` to start truly empty.

### Point an MCP client at it

Most MCP clients want a command to launch. Ours is:

```bash
python3 -m paper_binder_index.server --data-dir /path/to/your/papers/data
```

Or let it default to `./data` next to where you start it. Stdout is the
protocol; progress notes go to stderr.

## Tools the agent gets

| Tool | What it does |
| --- | --- |
| `add_document` | Log a document: title, type, binder/drawer/safe, tab, copy locations, refresh cadence |
| `where_is` | "where is my deed?" — returns every matching document with its exact location |
| `search_documents` | Search any word across titles, tabs, types, containers, notes |
| `missing_backup` | List originals with no backup copy recorded |
| `refresh_report` | Yearly refresh reminders: overdue, due soon, never refreshed |
| `mark_refreshed` | Record that a document was checked on a date |
| `set_copy_locations` | Add or replace a document's backup copy locations |
| `delete_document` | Remove a document by id |
| `binder_toc` | Printable table of contents (plain text/Markdown) |
| `set_binder_name` | Rename the heading used in the TOC |
| `overview` | One table with every document and its status |

## Smoke-test guide (copy/paste, 2 minutes)

The script below drives the real server process end to end. Scripts are
commands you can run; nothing here touches any network.

**1. Unit tests (fast, offline):**

```bash
python3 -m unittest discover -s tests -v
```

Expected: `Ran 53 tests ... OK`.

**2. Full smoke run:**

```bash
bash smoke_test.sh
```

Expected: a stack of `ok:` lines ending with `Smoke test PASSED`. It starts
the server, asks it where the sample deed lives, flags the unbacked car
title, prints the binder TOC, adds a document, and proves the auto-backup
file appeared.

**3. Manual one-liner (if you like typing):**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"where_is","arguments":{"document":"passport"}}}' \
  | PAPER_BINDER_DATA_DIR=/tmp/papers python3 -m paper_binder_index.server
```

**4. Check the storage:**

```bash
ls data/          # paper_binder.json + paper_binder.json.bak + backups/
python3 -m json.tool data/paper_binder.json | head
```

## Data & privacy

- Everything stays in `--data-dir`; delete that folder and the index is gone.
- The store is for **locations, not values**: keep account numbers, PINs,
  and passwords out of titles and notes. Inputs are length-limited and
  control-character-stripped, but no tool can protect data you paste in.
- License: MIT — see `LICENSE`. Copyright (c) 2026 Asdale Dynamics, LLC.

## Known gaps

See `STATUS.md` for the honest list.
