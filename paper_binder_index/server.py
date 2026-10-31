"""Paper Binder Index — a small stdio MCP server.

Indexes where physical paperwork lives (deeds, titles, passports,
policies...) so an agent can answer "where do I keep my house deed?"

Run it, point your MCP client at it, done. No network, no cloud, no
secrets. Storage is one offline JSON file with rolling backups.

Protocol: JSON-RPC 2.0, one JSON message per line on stdin/stdout.
Implements initialize, ping, tools/list, tools/call.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import store as store_module

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "paper-binder-index"
SERVER_VENDOR = "Asdale Dynamics, LLC"

_SAMPLE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sample_data",
    "sample_binder.json",
)


def _sample_path():
    return _SAMPLE if os.path.isfile(_SAMPLE) else None


def _tool_schema(description, properties, required=None):
    return {
        "annotations": {"readOnlyHint": False},
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required or [],
        },
    }


TOOLS = [
    {
        "name": "add_document",
        "description": (
            "Log a physical document: which binder or drawer it lives in, "
            "which tab, and any copy locations (safe, cloud scan, etc.)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "e.g. 'House deed'",
                },
                "doc_type": {
                    "type": "string",
                    "enum": sorted(store_module.DOCUMENT_TYPES),
                },
                "container": {
                    "type": "string",
                    "enum": sorted(store_module.CONTAINER_KINDS),
                    "description": "binder, drawer, safe or box",
                },
                "container_name": {
                    "type": "string",
                    "description": "e.g. 'Fireproof Home Binder' or 'File drawer 2'",
                },
                "tab": {"type": "string", "description": "Binder tab, optional"},
                "copy_locations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Where backup copies live; empty = flagged",
                },
                "refresh_interval_days": {
                    "type": "integer",
                    "enum": [90, 180, 365, 730],
                },
                "last_refreshed": {
                    "type": "string",
                    "description": "YYYY-MM-DD, optional",
                },
                "notes": {"type": "string"},
            },
            "required": ["title", "doc_type", "container_name"],
        },
    },
    {
        "name": "search_documents",
        "description": "Search the index by any word (title, tab, type, drawer...).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "One or more words"}
            },
        },
    },
    {
        "name": "where_is",
        "description": (
            "Plain-language lookup: 'where is my deed?' Returns matching "
            "documents with their exact location."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document": {"type": "string", "description": "What to look for"}
            },
            "required": ["document"],
        },
    },
    {
        "name": "missing_backup",
        "description": (
            "List documents that have no backup copy recorded "
            "(originals living dangerously)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "overview",
        "description": "Full table view: every document, its location, copies, and status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "refresh_report",
        "description": (
            "Yearly refresh reminders: what is overdue, what is coming due "
            "soon, and never-refreshed records that need a first date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Window for 'due soon' (default 60)",
                }
            },
        },
    },
    {
        "name": "mark_refreshed",
        "description": "Record that a document was checked/refreshed today (or a given date).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD, default today"},
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "set_copy_locations",
        "description": "Add or replace the backup copy locations for a document.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string"},
                "copy_locations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["doc_id", "copy_locations"],
        },
    },
    {
        "name": "delete_document",
        "description": "Remove a document from the index by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "binder_toc",
        "description": (
            "Emit a printable binder table of contents (Markdown/plain text) "
            "suitable for slipping into the front of a physical binder."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_binder_name",
        "description": "Rename the binder heading used in the table of contents.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
]

_TOOL_NAMES = {tool["name"] for tool in TOOLS}


class Toolbox:
    """Wires MCP tool calls to SafeStore methods."""

    def __init__(self, store):
        self.store = store

    def list_tool_names(self):
        return list(_TOOL_NAMES)

    def call(self, name, args):
        store = self.store
        if name == "add_document":
            doc = store.add_document(args)
            return "Logged %r as %s. Copies: %s" % (
                doc["title"],
                doc["id"],
                ", ".join(doc["copy_locations"]) or "none — needs a backup!",
            )
        if name == "search_documents":
            hits = store.search(args.get("query"))
            return self._format(hits, empty="Nothing matched that search.")
        if name == "where_is":
            hits = store.where_is(args.get("document"))
            return self._format(hits)
        if name == "missing_backup":
            hits = store.missing_backup()
            return self._format(
                hits, empty="All documents have a backup copy recorded. Nice."
            )
        if name == "overview":
            return self._format(store.list_documents(), empty="Index is empty.")
        if name == "refresh_report":
            days_ahead = args.get("days_ahead") or 60
            if not isinstance(days_ahead, int) or isinstance(days_ahead, bool):
                raise store_module.StoreError("days_ahead must be a whole number.")
            overdue, due_soon, current = store.refresh_report(days_ahead=days_ahead)
            lines = ["Paper refresh status:"]
            lines.append("Overdue (%d):" % len(overdue))
            for doc in overdue:
                lines.append("  - %s due %s" % (doc["title"], doc["refresh_due"]))
            lines.append("Due soon (%d):" % len(due_soon))
            for doc in due_soon:
                lines.append("  - %s due %s" % (doc["title"], doc["refresh_due"]))
            never = [d for d in current if not d["last_refreshed"]]
            if never:
                lines.append("Never refreshed on record (%d):" % len(never))
                for doc in never:
                    lines.append("  - %s %s" % (doc["title"], doc["id"]))
            if not overdue and not due_soon and not never:
                lines.append("  - nothing! everything is current.")
            return "\n".join(lines)
        if name == "mark_refreshed":
            doc = store.mark_refreshed(args.get("doc_id"), args.get("date"))
            return "%s marked refreshed on %s." % (doc["title"], doc["last_refreshed"])
        if name == "set_copy_locations":
            doc = store.set_copy_locations(args.get("doc_id"), args.get("copy_locations"))
            return "Copies for %s now: %s" % (
                doc["title"],
                ", ".join(doc["copy_locations"]) or "(none)",
            )
        if name == "delete_document":
            store.delete_document(args.get("doc_id"))
            return "Removed %s from the index." % args.get("doc_id")
        if name == "binder_toc":
            return store.binder_toc()
        if name == "set_binder_name":
            store.set_binder_name(args.get("name"))
            return "Binder renamed to %r." % args.get("name")
        raise store_module.StoreError("Unknown tool: %s" % name)

    def _format(self, docs, empty="Nothing found."):
        if not docs:
            return empty
        lines = ["%d match(es):" % len(docs)]
        for doc in docs:
            where = "%s: %s" % (doc["container"], doc["container_name"])
            if doc["tab"]:
                where += " > tab %s" % doc["tab"]
            copies = ", ".join(doc["copy_locations"]) or "no copy recorded"
            lines.append(
                "- %s (%s, %s) | %s | copies: %s"
                % (doc["title"], doc["id"], doc["doc_type"], where, copies)
            )
        return "\n".join(lines)


class MCPServer:
    """Line-delimited JSON-RPC 2.0 over stdin/stdout."""

    def __init__(self, toolbox, reader=None, writer=None):
        self.toolbox = toolbox
        self.reader = reader or sys.stdin
        self.writer = writer or sys.stdout
        self._continue = True

    # -- outgoing ----------------------------------------------------------

    def send(self, payload):
        line = json.dumps(payload, separators=(",", ":"))
        self.writer.write(line + "\n")
        self.writer.flush()

    def result(self, request_id, text):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        )

    def tool_error(self, request_id, message):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": message}],
                    "isError": True,
                },
            }
        )

    def error(self, request_id, code, message):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    # -- request handling ---------------------------------------------------

    def dispatch(self, message):
        """Handle one message object; returns False when the server should stop."""
        if not isinstance(message, dict):
            self.send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid request object"},
                }
            )
            return True
        method = message.get("method")
        request_id = message.get("id")
        is_notification = "id" not in message
        try:
            if method == "initialize":
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": SERVER_NAME,
                                "vendor": SERVER_VENDOR,
                                "version": store_module.VERSION,
                            },
                        },
                    }
                )
            elif method == "notifications/initialized":
                pass  # notification, nothing to answer
            elif method == "ping":
                self.send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            elif method == "tools/list":
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"tools": TOOLS},
                    }
                )
            elif method == "tools/call":
                name = (message.get("params") or {}).get("name")
                args = (message.get("params") or {}).get("arguments") or {}
                if name not in _TOOL_NAMES:
                    if is_notification:
                        return True
                    self.error(
                        request_id, -32602, "Unknown tool: %s" % name
                    )
                else:
                    try:
                        result = self.toolbox.call(name, args)
                    except store_module.StoreError as exc:
                        if is_notification:
                            return True
                        self.tool_error(request_id, str(exc))
                    else:
                        if not is_notification:
                            self.result(request_id, result)
            elif method == "shutdown":
                self._continue = False
                if not is_notification:
                    self.send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            else:
                if not is_notification:
                    self.error(request_id, -32601, "Unknown method: %s" % method)
        except Exception as exc:  # never crash the loop on one bad message
            if not is_notification:
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32603,
                            "message": "Internal error: %s" % exc,
                        },
                    }
                )
        return self._continue

    def handle_line(self, line):
        line = line.strip()
        if not line:
            return True
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.send(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            return True
        return self.dispatch(message)

    def serve(self):
        while self._continue:
            line = self.reader.readline()
            if not line and self.reader is sys.stdin:
                break
            if isinstance(line, bytes):
                line = line.decode("utf-8", "replace")
            if not line:
                # EOF from a file-based reader: finish politely.
                break
            self.handle_line(line)


def build_default_data_dir():
    """Data lives next to the project by default: ./data"""
    base = os.environ.get("PAPER_BINDER_DATA_DIR")
    if base:
        return base
    return os.path.join(os.getcwd(), "data")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="paper-binder-index",
        description="Paper Binder Index MCP server: index where physical documents live.",
    )
    parser.add_argument(
        "--data-dir",
        default=build_default_data_dir(),
        help="Directory for paper_binder.json and backups (default: ./data)",
    )
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help="Do not seed the data directory with the shipped sample binder.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + store_module.VERSION,
    )
    args = parser.parse_args(argv)

    seed = _sample_path()
    target = os.path.join(args.data_dir, "paper_binder.json")
    if seed and not os.path.exists(target) and not args.no_sample:
        os.makedirs(args.data_dir, exist_ok=True)
        shutil.copyfile(seed, target)
        print("Seeded %s with the sample binder." % target, file=sys.stderr)

    store = store_module.SafeStore(args.data_dir)
    toolbox = Toolbox(store)
    server = MCPServer(toolbox)
    # Log to stderr only; stdout is the protocol channel.
    print(
        "paper-binder-index %s serving %s" % (store_module.VERSION, store.path),
        file=sys.stderr,
    )
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
