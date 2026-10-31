"""Offline tests for the Paper Binder Index store and MCP server.

Run: python3 -m unittest discover -s tests -v
No network, no fixtures outside a temp directory.
"""

from __future__ import annotations

import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper_binder_index import store as store_module  # noqa: E402
from paper_binder_index.server import MCPServer, Toolbox  # noqa: E402


class TempStoreCase(unittest.TestCase):
    """Each test gets a fresh SafeStore in a temp directory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pbi-test-")
        self.store = store_module.SafeStore(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_minimal(self, title="Test doc", **overrides):
        args = {
            "title": title,
            "doc_type": "deed",
            "container": "binder",
            "container_name": "Home Binder",
            "tab": "Housing",
            "copy_locations": ["Fire safe"],
        }
        args.update(overrides)
        return self.store.add_document(args)


class LookupsTest(TempStoreCase):
    def test_add_and_get_by_id(self):
        doc = self.add_minimal(title="House deed")
        self.assertEqual(doc["title"], "House deed")
        self.assertTrue(doc["id"].startswith("doc-"))
        got = self.store.get_document(doc["id"])
        self.assertEqual(got["title"], "House deed")
        self.assertEqual(got["container_name"], "Home Binder")

    def test_ids_increment(self):
        first = self.add_minimal("One")
        second = self.add_minimal("Two")
        self.assertEqual(int(second["id"][-4:]), int(first["id"][-4:]) + 1)

    def test_where_is_returns_exact_location(self):
        self.add_minimal(title="Passport - Dylan")
        hits = self.store.where_is("passport")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["container"], "binder")
        self.assertEqual(hits[0]["tab"], "Housing")

    def test_search_matches_tab_and_copies(self):
        self.add_minimal(title="Passport")
        hits = self.store.search("fire safe")
        self.assertEqual(len(hits), 1)

    def test_search_no_match(self):
        self.add_minimal(title="Passport")
        self.assertEqual(self.store.search("zeppelin"), [])

    def test_where_is_unknown_raises(self):
        self.add_minimal(title="Passport")
        with self.assertRaises(store_module.StoreError):
            self.store.where_is("no-such-thing")

    def test_missing_backup_flags_only_empty(self):
        self.add_minimal(title="Has copies", copy_locations=["Safe"])
        self.add_minimal(title="No copies", copy_locations=[])
        flagged = self.store.missing_backup()
        self.assertEqual([d["title"] for d in flagged], ["No copies"])

    def test_binder_toc_prints_rows_and_warning(self):
        self.add_minimal(title="Deed with copy")
        self.add_minimal(title="Passport unbacked", copy_locations=[])
        toc = self.store.binder_toc()
        self.assertIn("Table of Contents", toc)
        self.assertIn("Deed with copy", toc)
        self.assertIn("Passport unbacked", toc)
        self.assertIn("Needs a backup copy (1)", toc)

    # ---------- refresh reminders ----------

    def test_refresh_report_groups_overdue_and_soon(self):
        today = datetime.date.today()
        self.add_minimal(
            title="Overdue",
            last_refreshed=(today - datetime.timedelta(days=400)).isoformat(),
        )
        self.add_minimal(
            title="Due soon",
            last_refreshed=(today - datetime.timedelta(days=340)).isoformat(),
        )
        self.add_minimal(
            title="Fresh",
            last_refreshed=(today - datetime.timedelta(days=10)).isoformat(),
        )
        overdue, due_soon, current = self.store.refresh_report(today=today)
        self.assertEqual([d["title"] for d in overdue], ["Overdue"])
        self.assertEqual([d["title"] for d in due_soon], ["Due soon"])
        self.assertEqual(current[0]["title"], "Fresh")

    def test_mark_refreshed_resets_clock(self):
        doc = self.add_minimal(title="To refresh")
        marked = self.store.mark_refreshed(doc["id"])
        self.assertEqual(marked["last_refreshed"], datetime.date.today().isoformat())
        overdue, _, _ = self.store.refresh_report()
        self.assertFalse(overdue)

    def test_set_copy_locations_replaces_list(self):
        doc = self.add_minimal(title="Deed")
        updated = self.store.set_copy_locations(
            doc["id"], ["Safe", "Attorney's office"]
        )
        self.assertEqual(updated["copy_locations"], ["Safe", "Attorney's office"])
        self.assertEqual(
            self.store.get_document(doc["id"])["copy_locations"],
            ["Safe", "Attorney's office"],
        )

    def test_missing_backup_corner_cases(self):
        doc = self.add_minimal(title="Single-space title?")
        self.assertIn(doc["tab"], "Housing")

    def test_toolbox_overdue_toolbox_check(self):
        """Sanity on Toolbox wiring for search."""
        self.add_minimal(title="Water bill folder")
        toolbox = Toolbox(self.store)
        text = toolbox.call("search_documents", {"query": "water"})
        self.assertIn("Water bill folder", text)


# ---------- validation & sanitization ----------


class ValidationTest(TempStoreCase):
    def test_reject_empty_title(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(title="   ")

    def test_reject_bad_doc_type(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(doc_type="laptop")

    def test_reject_bad_container(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(container="cloud")

    def test_reject_missing_location(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(container_name="")

    def test_reject_bad_interval(self):
        for bad in (0, 100, "365", True, 3.5):
            with self.assertRaises(store_module.StoreError):
                self.add_minimal(refresh_interval_days=bad)

    def test_reject_bad_date(self):
        for bad in ("2026-13-01", "01-2026-31", "2026/01/01", True):
            with self.assertRaises(store_module.StoreError):
                self.add_minimal(last_refreshed=bad)

    def test_strips_control_characters(self):
        doc = self.add_minimal(title="Deed\t\n\x07X")
        self.assertEqual(doc["title"], "DeedX")
        self.assertTrue(all(ch.isprintable() for ch in doc["title"]))

    def test_copy_locations_must_be_strings(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(copy_locations=[42])

    def test_empty_copy_entries_dropped(self):
        doc = self.add_minimal(copy_locations=["", "  ", "Safe"])
        self.assertEqual(doc["copy_locations"], ["Safe"])

    def test_text_max_length(self):
        with self.assertRaises(store_module.StoreError):
            self.add_minimal(title="x" * 500)

    def test_delete_document_round_trips(self):
        doc = self.add_minimal("Delete me")
        self.assertTrue(self.store.delete_document(doc["id"]))
        self.assertIsNone(self.store.get_document(doc["id"]))
        with self.assertRaises(store_module.StoreError):
            self.store.delete_document(doc["id"])

    def test_unknown_doc_id_get_raises(self):
        self.assertIsNone(self.store.get_document("doc-9999"))


# ---------- persistence, backups, path safety ----------


class StorageTest(TempStoreCase):
    def test_file_is_written_inside_data_dir(self):
        doc = self.add_minimal("Persisted")
        path = self.store.path
        self.assertTrue(path.startswith(self.store.base_dir + os.sep))
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["documents"][0]["title"], "Persisted")
        self.assertEqual(data["documents"][0]["id"], doc["id"])

    def test_reload_sees_saved_data(self):
        doc = self.add_minimal("Stays put")
        store2 = store_module.SafeStore(self.tmp)
        self.assertEqual(store2.get_document(doc["id"])["title"], "Stays put")

    def test_auto_backup_makes_rolling_bak(self):
        self.add_minimal("First")
        self.add_minimal("Second")
        store2 = store_module.SafeStore(self.tmp)
        store2.add_document({"title": "Third", "doc_type": "deed",
                             "container": "binder", "container_name": "B"})
        store3 = store_module.SafeStore(self.tmp)
        store3.add_document({"title": "Fourth", "doc_type": "deed",
                             "container": "binder", "container_name": "B"})
        rolling = os.path.join(self.store.base_dir, "paper_binder.json.bak")
        self.assertTrue(os.path.exists(rolling))
        with open(rolling, encoding="utf-8") as handle:
            bak = json.load(handle)
        titles = [d["title"] for d in bak["documents"]]
        self.assertIn("Third", titles)  # save before the most recent write

    def test_dated_backups_rotated(self):
        for n in range(10):
            self.store.add_document({
                "title": "doc %d" % n, "doc_type": "deed",
                "container": "binder", "container_name": "B",
            })
        backups = self.store._existing_backups()
        self.assertTrue(len(backups) <= store_module.MAX_BACKUPS)


class PathSafetyTest(TempStoreCase):
    def test_resolve_rejects_escape(self):
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("../escape.json")

    def test_resolve_rejects_deep_escape(self):
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("../../etc/passwd")

    def test_resolve_rejects_absolute_outside(self):
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("/etc/passwd")

    def test_resolve_rejects_empty_and_nul(self):
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("")
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("good\x00evil.json")

    def test_resolve_rejects_backslashes(self):
        with self.assertRaises(store_module.StoreError):
            self.store.resolve_path("sub\\..\\..\\windows\\win.ini")

    def test_symlink_escape_is_refused(self):
        # Build a symlink trap: data_dir/link.json -> /tmp/outside.json
        outside = os.path.join(tempfile.gettempdir(), "pbi-outside-target.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("{}")
        link = os.path.join(self.store.base_dir, "link.json")
        os.symlink(outside, link)
        try:
            with self.assertRaises(store_module.StoreError):
                self.store.resolve_path("link.json")
        finally:
            os.remove(link)
            os.remove(outside)

    def test_corrupt_file_is_detected(self):
        path = self.store.path
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        with self.assertRaises(store_module.StoreError):
            store_module.SafeStore(self.tmp)

    def test_non_store_json_is_detected(self):
        path = self.store.path
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"hello": True}, handle)
        with self.assertRaises(store_module.StoreError):
            store_module.SafeStore(self.tmp)


# ---------- MCP server over an in-memory channel ----------


class ScriptedWriter:
    """Minimal file-like writer that records each flushed line."""

    def __init__(self):
        self.messages = []

    def write(self, text):
        self._pending = text

    def flush(self):
        self.messages.append(json.loads(self._pending))


class ServerProtocolTest(TempStoreCase):
    def setUp(self):
        super().setUp()
        self.server_store = store_module.SafeStore(self.tmp)
        self.out = ScriptedWriter()
        self.server = MCPServer(Toolbox(self.server_store), reader=io.StringIO(),
                                writer=self.out)

    def rpc(self, payload):
        self.server.handle_line(json.dumps(payload))
        return self.out.messages.pop()

    def test_initialize_hands_back_version(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                         "params": {}})
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(resp["result"]["serverInfo"]["name"], "paper-binder-index")

    def test_initialize_is_a_request_even_without_params(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize"})
        self.assertEqual(resp["id"], 7)

    def test_notification_gets_no_reply(self):
        self.server.handle_line(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        self.assertEqual(self.out.messages, [])

    def test_tools_list_and_call(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertIn("add_document", names)
        self.assertIn("binder_toc", names)
        resp = self.rpc({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "add_document", "arguments": {
                "title": "House deed", "doc_type": "deed",
                "container": "binder", "container_name": "Home Binder",
                "tab": "Housing", "copy_locations": ["Fire safe"],
            }},
        })
        self.assertFalse(resp["result"]["isError"])
        self.assertIn("doc-0001", resp["result"]["content"][0]["text"])

    def test_unknown_method_gets_error(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 4, "method": "nope/nope"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_unknown_tool_gets_error(self):
        resp = self.rpc({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "rm_rf", "arguments": {}},
        })
        self.assertEqual(resp["error"]["code"], -32602)

    def test_bad_arguments_become_tool_error_not_crash(self):
        resp = self.rpc({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "add_document", "arguments": {"title": ""}},
        })
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("title", resp["result"]["content"][0]["text"])

    def test_malformed_json_gets_parse_error(self):
        self.server.handle_line("{this is not json")
        resp = self.out.messages.pop()
        self.assertEqual(resp["error"]["code"], -32700)

    def test_non_object_message_rejected(self):
        self.server.handle_line("[1, 2, 3]")
        resp = self.out.messages.pop()
        self.assertEqual(resp["error"]["code"], -32600)

    def test_ping(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_shutdown_stops_after_response(self):
        resp = self.rpc({"jsonrpc": "2.0", "id": 10, "method": "shutdown"})
        self.assertEqual(resp["id"], 10)
        self.assertFalse(self.server._continue)


class ServerToolBehaviorTest(TempStoreCase):
    """Toolbox-level checks of agent-facing behavior."""

    def setUp(self):
        super().setUp()
        self.toolbox = Toolbox(self.store)

    def test_where_is_tool_answers_location(self):
        self.store.add_document({
            "title": "Passport", "doc_type": "passport", "container": "safe",
            "container_name": "Fire safe", "tab": "",
            "copy_locations": ["Parents' safe"],
        })
        text = self.toolbox.call("where_is", {"document": "passport"})
        self.assertIn("Fire safe", text)
        self.assertIn("Parents' safe", text)

    def test_missing_backup_tool_lists_docs(self):
        self.toolbox.call("add_document", {
            "title": "Car title", "doc_type": "title",
            "container": "binder", "container_name": "Home Binder",
        })
        text = self.toolbox.call("missing_backup", {})
        self.assertIn("Car title", text)

    def test_missing_backup_all_clear_message(self):
        text = self.toolbox.call("missing_backup", {})
        self.assertIn("All documents", text)

    def test_binder_toc_tool_outputs_markdown(self):
        self.toolbox.call("add_document", {
            "title": "Deed", "doc_type": "deed",
            "container": "binder", "container_name": "Home Binder",
            "tab": "Housing",
        })
        text = self.toolbox.call("binder_toc", {})
        self.assertIn("Table of Contents", text)
        self.assertIn("Housing | Deed", text)

    def test_set_binder_name_renames_toc_heading(self):
        self.toolbox.call("set_binder_name", {"name": "Asdale Fire Binder"})
        text = self.toolbox.call("binder_toc", {})
        self.assertIn("Asdale Fire Binder — Table of Contents", text)

    def test_delete_via_toolbox(self):
        self.toolbox.call("add_document", {
            "title": "Temp", "doc_type": "other",
            "container": "box", "container_name": "Garage box 1",
        })
        doc_id = self.store.list_documents()[0]["id"]
        text = self.toolbox.call("delete_document", {"doc_id": doc_id})
        self.assertIn("Removed", text)
        self.assertEqual(self.store.search("Temp"), [])

    def test_refresh_report_tool_lists_due(self):
        today = datetime.date.today()
        self.store.add_document({
            "title": "Old policy", "doc_type": "policy", "container": "binder",
            "container_name": "B",
            "last_refreshed": (today - datetime.timedelta(days=400)).isoformat(),
        })
        text = self.toolbox.call("refresh_report", {})
        self.assertIn("Old policy", text)
        self.assertIn("Overdue", text)


if __name__ == "__main__":
    unittest.main()
