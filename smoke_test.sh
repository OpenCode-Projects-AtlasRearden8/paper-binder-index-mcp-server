#!/usr/bin/env bash
# Paper Binder Index — smoke test.
# Spins the MCP server up for one scripted session and checks the answers.
# Usage:  bash smoke_test.sh
set -euo pipefail

cd "$(dirname "$0")"
TMPDIR_SMOKE="$(mktemp -d /tmp/paper-binder-smoke.XXXXXX)"
trap 'rm -rf "$TMPDIR_SMOKE"' EXIT

SESSION=$(mktemp /tmp/paper-binder-smoke-session.XXXXXX)
trap 'rm -rf "$TMPDIR_SMOKE" "$SESSION"' EXIT

cat >"$SESSION" <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"where_is","arguments":{"document":"deed"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"missing_backup","arguments":{}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"binder_toc","arguments":{}}}
{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"refresh_report","arguments":{}}}
{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"add_document","arguments":{"title":"Smoke test note","doc_type":"other","container":"binder","container_name":"Scratch Binder"}}}
EOF

PAPER_BINDER_DATA_DIR="$TMPDIR_SMOKE" python3 -m paper_binder_index.server \
  <"$SESSION" >"$TMPDIR_SMOKE/stdout.log" 2>"$TMPDIR_SMOKE/stderr.log"

output="$TMPDIR_SMOKE/stdout.log"
check() {
  if grep -q "$1" "$output"; then
    echo "ok: $2"
  else
    echo "FAIL: expected '$1' in server output" >&2
    cat "$output" >&2
    exit 1
  fi
}

check '"protocolVersion":' "initialize answered"
check '"tools":' "tools/list answered"
check 'House deed' "where_is found the sample deed"
check 'Car title' "missing_backup flagged the unbacked car title"
check 'Table of Contents' "binder_toc produced the printable index"
check 'Overdue' "refresh_report ran"

# A response was sent for every request id.
for n in 1 2 3 4 5 6 7; do
  check "\"id\":$n\," "request $n got a response"
done

check 'doc-' "add_document logged a new document"

# The data file and rolling backup exist inside the data dir only.
test -f "$TMPDIR_SMOKE/paper_binder.json" && echo "ok: data file written"
test -f "$TMPDIR_SMOKE/paper_binder.json.bak" || {
  echo "FAIL: rolling .bak missing" >&2; exit 1; }
echo "ok: auto-backup present"

echo
echo "Smoke test PASSED ($(grep -c '"jsonrpc"' "$output") JSON-RPC messages)."
