"""Core storage for the Paper Binder Index MCP server.

Everything lives in a single JSON file inside a fixed data directory.
The store is deliberately simple and local-first:

- Path safety: no file may be read from or written outside the data
  directory, no matter what a caller (or a model) asks for.
- Auto-backup: every write rotates a small set of backups first, so a
  bad save can always be undone.
- No secrets: the file format holds only document locations, never
  account numbers or passwords. Callers are on their honor to keep it
  that way; the sanitizer strips anything that looks like a secret key.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil

VERSION = "1.0.0"
MAX_TEXT_LEN = 200
MAX_LIST_TEXT_LEN = 80
MAX_ITEMS = 2000
MAX_BACKUPS = 5
DOC_ID_PREFIX = "doc-"
DATE_FORMAT = "%Y-%m-%d"
ALLOWED_INTERVALS = (90, 180, 365, 730)

DOCUMENT_TYPES = (
    "deed",
    "title",
    "passport",
    "policy",
    "birth certificate",
    "marriage certificate",
    "social security card",
    "will",
    "medical record",
    "tax record",
    "financial statement",
    "other",
)

CONTAINER_KINDS = ("binder", "drawer", "safe", "box")

# Keys whose values should never be stored verbatim, even if someone pastes one in.
SECRET_KEY_PATTERN = re.compile(
    r"(?i)^(api[_-]?key|secret|password|passwd|token|credential|ssn|dob)$"
)


class StoreError(Exception):
    """Raised for user-facing storage problems (bad input, bad path)."""


class SafeStore:
    """A JSON file store locked inside a single data directory."""

    def __init__(self, data_dir, filename="paper_binder.json"):
        self.base_dir = os.path.realpath(os.path.abspath(data_dir))
        os.makedirs(self.base_dir, exist_ok=True)
        self.path = self._resolve_within(self.base_dir, filename)
        self._data = None
        self.load()

    # ---------- path safety ----------

    @staticmethod
    def _resolve_within(base_dir, candidate):
        """Resolve *candidate* and require it to stay inside *base_dir*.

        Rejects absolute paths, '..' components, backslashes, NUL bytes,
        and symlink escapes. Returns a real (symlink-free) path.
        """
        if not isinstance(candidate, str) or not candidate:
            raise StoreError("A file path must be a non-empty string.")
        if "\x00" in candidate:
            raise StoreError("File paths may not contain NUL bytes.")
        if "\\" in candidate:
            raise StoreError("Backslashes are not allowed in file paths.")
        joined = candidate if os.path.isabs(candidate) else os.path.join(
            base_dir, candidate
        )
        candidate_real = os.path.realpath(os.path.abspath(joined))
        base_real = os.path.realpath(os.path.abspath(base_dir))
        try:
            inside = os.path.commonpath([candidate_real, base_real]) == base_real
        except ValueError:  # different drives / disjoint roots
            inside = False
        parent_real = os.path.realpath(os.path.dirname(candidate_real))
        real_inside = (
            parent_real == base_real
            or parent_real.startswith(base_real + os.sep)
        )
        if not inside or not real_inside or candidate_real == base_real:
            raise StoreError(
                "Path escapes the data directory: %r" % candidate
            )
        return candidate_real

    def resolve_path(self, candidate):
        """Public helper: resolve a candidate path inside the data dir."""
        return self._resolve_within(self.base_dir, candidate)

    # ---------- sanitizers ----------

    @staticmethod
    def _clean_text(value, max_len=MAX_TEXT_LEN):
        if not isinstance(value, str):
            raise StoreError("Expected text, got %r." % type(value).__name__)
        # Strip control characters (newline included: fields are single-line).
        cleaned = "".join(ch for ch in value if ch.isprintable())
        cleaned = cleaned.strip()
        if len(cleaned) > max_len:
            raise StoreError("Text is too long (max %d characters)." % max_len)
        return cleaned

    @classmethod
    def _clean_short(cls, value):
        return cls._clean_text(value, MAX_LIST_TEXT_LEN)

    # ---------- document schema ----------

    @staticmethod
    def _clean_date(value, field):
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise StoreError("%s must be a string like 2026-01-31." % field)
        try:
            parsed = datetime.date.fromisoformat(value)
        except ValueError:
            raise StoreError(
                "%s must look like YYYY-MM-DD, got %r." % (field, value)
            )
        if parsed.isoformat() != value:
            raise StoreError("%s must look exactly like YYYY-MM-DD." % field)
        return value

    def _build_document(self, args):
        title = self._clean_text(args.get("title") or "")
        if not title:
            raise StoreError("A document needs a title.")
        doc_type = self._clean_short(args.get("doc_type") or "").lower()
        if doc_type not in DOCUMENT_TYPES:
            raise StoreError(
                "doc_type must be one of: %s."
                % ", ".join(x for x in DOCUMENT_TYPES)
            )
        container = (args.get("container") or "binder").lower()
        if container not in CONTAINER_KINDS:
            raise StoreError(
                "container must be one of: %s." % ", ".join(CONTAINER_KINDS)
            )
        container_name = self._clean_short(args.get("container_name") or "")
        if not container_name:
            raise StoreError(
                "Say where it lives, e.g. container_name: 'Fireproof Home Binder'."
            )
        tab = self._clean_short(args.get("tab") or "")
        copy_locations = args.get("copy_locations") or []
        if not isinstance(copy_locations, list):
            raise StoreError("copy_locations must be a list of short strings.")
        cleaned_copies = []
        for item in copy_locations[:20]:
            if item in (None, ""):
                continue
            if not isinstance(item, str):
                raise StoreError("copy_locations must be a list of strings.")
            cleaned = self._clean_short(item)
            if cleaned:
                cleaned_copies.append(cleaned)
        refresh_interval = args.get("refresh_interval_days")
        if refresh_interval is None:
            refresh_interval = 365
        if not isinstance(refresh_interval, int) or isinstance(
            refresh_interval, bool
        ):
            raise StoreError("refresh_interval_days must be a whole number.")
        if refresh_interval not in ALLOWED_INTERVALS:
            raise StoreError(
                "refresh_interval_days must be one of %d, %d, %d, %d."
                % ALLOWED_INTERVALS
            )
        last_refreshed = self._clean_date(args.get("last_refreshed"), "last_refreshed")
        notes = self._clean_short(args.get("notes") or "") or ""
        return {
            "id": args.get("id"),
            "title": title,
            "doc_type": doc_type,
            "container": container,
            "container_name": container_name,
            "tab": tab,
            "copy_locations": cleaned_copies,
            "refresh_interval_days": refresh_interval,
            "last_refreshed": last_refreshed,
            "notes": notes,
        }

    # ---------- load / save ----------

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                self._data = json.load(handle)
        except FileNotFoundError:
            self._data = {
                "version": VERSION,
                "binder_name": "Household Paper Binder",
                "documents": [],
            }
        except (json.JSONDecodeError, OSError) as exc:
            raise StoreError("Could not read %s: %s" % (self.path, exc))
        if not isinstance(self._data, dict) or not isinstance(
            self._data.get("documents"), list
        ):
            raise StoreError(
                "%s is not a Paper Binder Index file." % self.path
            )
        return self._data

    def _backup(self):
        """Rotate the current file into .bak files before overwriting it."""
        if not os.path.exists(self.path):
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        timestamped = os.path.join(
            self.base_dir, "backups", "%s.%s.bak" % (os.path.basename(self.path), stamp)
        )
        os.makedirs(os.path.join(self.base_dir, "backups"), exist_ok=True)
        shutil.copy2(self.path, timestamped)
        # Also keep a simple rolling .bak one save behind.
        rolling = os.path.join(self.base_dir, os.path.basename(self.path) + ".bak")
        shutil.copy2(self.path, rolling)
        backups = sorted(self._existing_backups())
        while len(backups) > MAX_BACKUPS:
            os.remove(backups[0])
            backups = backups[1:]

    def _existing_backups(self):
        folder = os.path.join(self.base_dir, "backups")
        if not os.path.isdir(folder):
            return []
        prefix = os.path.basename(self.path) + "."
        return [
            os.path.join(folder, name)
            for name in os.listdir(folder)
            if name.startswith(prefix) and name.endswith(".bak")
        ]

    def save(self):
        self._backup()
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_path, self.path)

    # ---------- binder metadata ----------

    def set_binder_name(self, name):
        name = self._clean_text(name or "")
        if not name:
            raise StoreError("The binder needs a name.")
        self._data["binder_name"] = name
        self.save()

    # ---------- CRUD ----------

    def _next_id(self):
        highest = 0
        for doc in self._data["documents"]:
            doc_id = str(doc.get("id") or "")
            if doc_id.startswith(DOC_ID_PREFIX):
                digits = doc_id[len(DOC_ID_PREFIX) :]
                if digits.isdigit():
                    highest = max(highest, int(digits))
        return "%s%04d" % (DOC_ID_PREFIX, highest + 1)

    def add_document(self, args):
        if len(self._data["documents"]) >= MAX_ITEMS:
            raise StoreError("The index is full (%d items)." % MAX_ITEMS)
        doc = self._build_document(args)
        doc["id"] = self._next_id()
        self._data["documents"].append(doc)
        self.save()
        return doc

    def get_document(self, doc_id):
        doc_id = self._clean_short(str(doc_id))
        for doc in self._data["documents"]:
            if str(doc.get("id")) == doc_id:
                return dict(doc)
        return None

    def delete_document(self, doc_id):
        doc_id = self._clean_short(str(doc_id))
        kept = [d for d in self._data["documents"] if str(d.get("id")) != doc_id]
        removed = len(kept) != len(self._data["documents"])
        if not removed:
            raise StoreError("No document has id %s." % doc_id)
        self._data["documents"] = kept
        self.save()
        return removed

    def set_copy_locations(self, doc_id, copy_locations):
        doc = self.get_document(doc_id)
        if doc is None:
            raise StoreError("No document has id %r." % str(doc_id))
        for other in self._data["documents"]:
            if str(other.get("id")) == str(doc.get("id")):
                other["copy_locations"] = self._build_document(
                    {**other, "copy_locations": copy_locations}
                )["copy_locations"]
                self.save()
                return dict(other)
        raise StoreError("No document has id %r." % str(doc_id))

    def mark_refreshed(self, doc_id, date=None):
        doc = self.get_document(doc_id)
        if doc is None:
            raise StoreError("No document has id %r." % str(doc_id))
        when = self._clean_date(date or datetime.date.today().isoformat(), "date")
        for other in self._data["documents"]:
            if str(other.get("id")) == str(doc.get("id")):
                other["last_refreshed"] = when
                self.save()
                return dict(other)
        raise StoreError("No document has id %r." % str(doc_id))

    def refresh_document(self, doc_id, date=None):
        """Alias kept for clarity: same as marking a document refreshed."""
        return self.mark_refreshed(doc_id, date)

    # ---------- lookups ----------

    def list_documents(self):
        return [dict(d) for d in self._data["documents"]]

    def search(self, query):
        needle = self._clean_text(query or "").lower()
        if not needle:
            return self.list_documents()
        hits = []
        for doc in self._data["documents"]:
            haystack = " ".join(
                [
                    doc["id"],
                    doc["title"],
                    doc["doc_type"],
                    doc["container"],
                    doc["container_name"],
                    doc["tab"],
                    " ".join(doc["copy_locations"]),
                    doc["notes"],
                ]
            ).lower()
            if needle in haystack:
                hits.append(dict(doc))
        return hits

    def where_is(self, title_fragment):
        """Plain-language lookup: 'where is my deed?'"""
        hits = self.search(title_fragment)
        if not hits:
            raise StoreError(
                'Nothing matching %r is in the index. Try searching fewer words.'
                % str(title_fragment)
            )
        return hits

    def missing_backup(self):
        """Documents flagged as needing a backup copy."""
        return [
            dict(d) for d in self._data["documents"] if not d["copy_locations"]
        ]

    # ---------- reminders ----------

    @staticmethod
    def refresh_due_date(doc):
        if not doc.get("last_refreshed"):
            return None
        base = datetime.date.fromisoformat(doc["last_refreshed"])
        interval = int(doc.get("refresh_interval_days") or 365)
        return base + datetime.timedelta(days=interval)

    def refresh_report(self, days_ahead=60, today=None):
        """Return (overdue, due_soon, refreshed_today_ok)."""
        today = today or datetime.date.today()
        soon = today + datetime.timedelta(days=days_ahead)
        overdue, due_soon, current = [], [], []
        for doc in self._data["documents"]:
            due = self.refresh_due_date(doc)
            if due is None:
                # Never refreshed on record: treat as current but mention it.
                current.append(dict(doc))
                continue
            item = {**doc, "refresh_due": due.isoformat()}
            if due < today:
                overdue.append(item)
            elif due <= soon:
                due_soon.append(item)
            else:
                current.append(item)
        overdue.sort(key=lambda d: d["refresh_due"])
        due_soon.sort(key=lambda d: d["refresh_due"])
        return overdue, due_soon, current

    def unpublished_dates(self):
        """Docs with no last_refreshed at all (never logged a refresh)."""
        return [dict(d) for d in self._data["documents"] if not d["last_refreshed"]]

    # ---------- printable table of contents ----------

    def binder_toc(self):
        lines = []
        lines.append("%s — Table of Contents" % self._data.get("binder_name", "Binder"))
        lines.append("Printed: %s" % datetime.date.today().isoformat())
        lines.append("")
        lines.append("Tab | Title | Type | Where it lives | Copies")
        lines.append("---" * 1 + "-" * 65)
        docs = sorted(
            self._data["documents"],
            key=lambda d: (d["container_name"], d["tab"] or "~", d["title"]),
        )
        if not docs:
            lines.append("(no documents logged yet)")
        for doc in docs:
            where = "%s: %s" % (doc["container"], doc["container_name"])
            if doc["tab"]:
                where += " > tab %s" % doc["tab"]
            copies = ", ".join(doc["copy_locations"]) or "NO COPY — add one!"
            lines.append(
                "%s | %s | %s | %s | %s"
                % (
                    doc["tab"] or "-",
                    doc["title"],
                    doc["doc_type"],
                    where,
                    copies,
                )
            )
        missing = self.missing_backup()
        if missing:
            lines.append("")
            lines.append("Needs a backup copy (%d):" % len(missing))
            for doc in missing:
                lines.append("  - %s (%s)" % (doc["title"], doc["id"]))
        return "\n".join(lines) + "\n"
