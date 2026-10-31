# STATUS.md — Paper Binder Index

Updated: 2026-10-31

## What was built

A small, offline MCP server (Python standard library only, stdio) that
indexes where physical paperwork lives so an agent can answer
"where-things-live" questions: `where_is my deed?` returns "binder:
Fireproof Home Binder > tab Housing, copies: Fire safe, cloud scan".

Features:

- **Document logging** with binder-or-drawer/safe/box, container name, tab,
  copy locations, doc type (deed, title, passport, policy, etc.).
- **Missing-backup flagging** — `missing_backup` lists every original with
  no recorded copy; the binder TOC marks them.
- **Yearly refresh reminders** — `refresh_report` sorts documents into
  overdue / due soon / never-refreshed; `mark_refreshed` resets the clock.
  Intervals of 90/180/365/730 days are supported.
- **Printable binder table of contents** — `binder_toc` emits a
  plain-text/Markdown TOC grouped by container and tab.
- **JSON storage** (`paper_binder.json`) with:
  - **Auto-backup**: dated backups in `backups/` (capped at 5) plus one
    rolling `.bak`, written before every save; saves are atomic
    (tmp + `os.replace`).
  - **Path safety** (`SafeStore._resolve_within`): rejects `../` escapes,
    absolute paths outside the data dir, backslashes, NUL bytes, empty
    paths, and symlink escapes; corrupt or foreign JSON is refused.
- **Sample binder** (`sample_data/sample_binder.json`) auto-seeds the data
  dir on first run (`--no-sample` to skip).
- **MCP protocol**: JSON-RPC 2.0 over line-delimited stdio; implements
  `initialize`, `ping`, `notifications/initialized`, `tools/list`
  (11 tools), `tools/call`, `shutdown`. Malformed input becomes a proper
  JSON-RPC error (-32700/-32600/-32601/-32602); tool failures become
  `isError: true` results instead of crashes. Logs go to stderr only.

Complements digital-papers-handbook and free-emergency-binder-kit: this
covers the *physical* copies the digital trackers miss.

## Layout

```
paper_binder_index/store.py     storage, validation, path safety, TOC
paper_binder_index/server.py    MCP stdio server + tool wiring
sample_data/sample_binder.json  5-document sample
tests/test_paper_binder.py      55 offline unit tests
smoke_test.sh                   end-to-end scripted run against a real process
README.md / LICENSE / STATUS.md
```

## How to run it

- Tests: `python3 -m unittest discover -s tests` (Python 3.10+ tested on 3.13)
- Smoke test: `bash smoke_test.sh`
- Server: `python3 -m paper_binder_index.server --data-dir ./data`
  (default data dir is `./data` or `PAPER_BINDER_DATA_DIR` env var)
- Point an MCP client at that same command; stdout is the protocol.

## Verification results (this tree, Python 3.13.5)

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s tests` | **Ran 55 tests — OK** |
| `bash smoke_test.sh` | **PASSED** (7 JSON-RPC responses, all `ok:` checks) |
| Sample binder schema conformance (`_build_document` round-trip) | OK |
| Live session: initialize / tools-list / where_is / missing_backup / refresh_report / binder_toc / add_document | All answered correctly |
| Attempted lookup with `"../escape"` payload | Handled as a clean tool error, no crash |

Bugs found and fixed during verification: relative data-file paths
resolved against the wrong base (all tests initially failed); interval `0`
silently becoming the 365-day default; two test-side issues.

## Known gaps

- No full MCP capability negotiation beyond tools (no resources/prompts) —
  fine for the intended clients.
- No concurrent-write locking; safe for one client, as a local MCP server.
- Doc types/containers are fixed enums; extending them means editing
  `store.py` constants.
- `binder_toc` is plain text meant for printing; no PDF/HTML export.
- No CLI shell for humans (the MCP `tools/call` interface and tests are
  the interface); a human-friendly job would be a nice follow-up.
- Backups keep only the 5 most recent dated copies.
- Intentionally: no network, no encryption-at-rest — treat the data dir
  like a real folder of paper (and keep secrets out of it).
