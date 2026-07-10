## Memory (Hindsight)

You have access to the `hindsight` MCP tools: `retain`, `recall`, `reflect`.

Preferred path:
- If `hindsight` tools are exposed in the current Codex tool list, call them
  directly.

Fallback path when the tools are not surfaced directly:
- The hindsight MCP server is expected at `http://localhost:8888/mcp/hermes`
  from `~/.codex/config.toml`.
- Use raw MCP over HTTP from the terminal: first call `initialize`, capture the
  `mcp-session-id` response header, then send `notifications/initialized`, and
  finally call `tools/call` for `recall`, `reflect`, or `sync_retain`.
- Include the `mcp-session-id` header on every `tools/list` and `tools/call`
  request after `initialize`.
- Prefer `sync_retain` instead of `retain` when using the raw HTTP fallback so
  the memory is immediately available to later `recall` calls in the same task.
- If the MCP server responds and `tools/list` includes hindsight tools, but the
  current Codex session still does not expose them, treat that as a Codex client
  integration issue rather than a hindsight configuration issue.

Minimal raw MCP flow example:
```sh
session_id=$(curl -sS -D - -o /tmp/hindsight-init.txt \
  -X POST http://localhost:8888/mcp/hermes \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"codex","version":"1.0"}}}' \
  | awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' | tr -d '\r')

curl -sS -X POST http://localhost:8888/mcp/hermes \
  -H 'Content-Type: application/json' \
  -H "mcp-session-id: $session_id" \
  --data '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null

payload=$(jq -nc --arg query 'summarize the current task here' \
  '{jsonrpc:"2.0",id:2,method:"tools/call",params:{name:"recall",arguments:{query:$query,budget:"high",max_tokens:1200}}}')

curl -sS -X POST http://localhost:8888/mcp/hermes \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "mcp-session-id: $session_id" \
  --data "$payload"
```

Workflow:
- At the start of every task, call `recall` with a query summarizing the task
  to fetch relevant project facts, decisions, and preferences.
- After finishing a task or reaching a decision worth keeping, call `retain`
  to store the outcome (what was decided + why + constraints).
- If using the raw HTTP fallback, use `sync_retain` for the write step.
- Use `reflect` when you need a synthesized answer drawing from multiple memories.

Tags convention:
- codex: origin
- decision / preference / architecture / bugfix: category
