# mt.py

A command-line client for the Vega observability MCP server. It can:

- list and export the server's tools (`tools/list`)
- call any tool (`tools/call`), print the result, mask credentials and return a meaningful exit code

It uses only the Python standard library and needs Python 3.8 or newer.

> **Where it works:** only inside the Vega agent runtime. Both endpoints
> (`http://127.0.0.1:8083` and `http://127.0.0.1:8082/mcp`) are loopback addresses,
> so you can't reach them from your own machine.

---

## Contents

1. [Setup](#1-setup)
2. [Command syntax](#2-command-syntax)
3. [Modes](#3-modes)
4. [Options](#4-options)
5. [Environment variables](#5-environment-variables)
6. [Exit codes](#6-exit-codes)
7. [Context headers and `-A`](#7-context-headers-and--a)
8. [Practical examples](#8-practical-examples)
9. [Using it in shell scripts](#9-using-it-in-shell-scripts)
10. [Troubleshooting](#10-troubleshooting)
11. [How it works](#11-how-it-works)
12. [Safety notes](#12-safety-notes)

---

## 1. Setup

```bash
# Save the script (paste the code into this file)
mkdir -p ~/mcp && cd ~/mcp
nano mcp_tools.py            # or any editor

chmod +x mcp_tools.py        # optional; you can also run it with python3

# Check that it runs and can reach the server
python3 mcp_tools.py -h
python3 mcp_tools.py n | head
```

Expected output of the last command (stderr shows the tool count):

```
87 tools from http://127.0.0.1:8083
archive_automated_rollout_config
create_alert
create_automated_rollout_config
...
```

> `/tmp` is wiped between agent runs. If you keep the script there, save a copy
> somewhere else as well.

---

## 2. Command syntax

```
python3 mcp_tools.py [options] MODE [arg]
```

- **Options go before `MODE`.**
- `arg` is a tool name (for `tool` and `call`) or a chunk size in bytes (for `chunks`).

---

## 3. Modes

| Mode       | Aliases    | `arg`                      | What it produces                                       |
|------------|------------|----------------------------|--------------------------------------------------------|
| `names`    | `n`, `ls`  | none                       | Sorted tool names on stdout                            |
| `tool`     | `t`        | tool name (required)       | Full JSON definition of one tool on stdout             |
| `call`     | `c`        | tool name (required)       | The tool's result payload on stdout                    |
| `raw`      | `r`        | none                       | `OUT/tools_raw.json`: the server's exact bytes         |
| `pretty`   | `p`        | none                       | `OUT/tools_raw_pretty.json`: indented                  |
| `nodesc`   | `nd`       | none                       | `OUT/tools_nodesc.json`: descriptions removed          |
| `schemas`  | `s`        | none                       | `OUT/tools_schemas.json`: `[{name, inputSchema}]`      |
| `per-tool` | `pt`       | none                       | `OUT/tools/<name>.json`: one file per tool             |
| `chunks`   | `ch`       | max bytes (default 45000)  | `OUT/part1.json` … `partN.json`, split at tool boundaries |
| `all`      | `a`        | none                       | Every file mode above (chunks at 45000 bytes)          |

`OUT` defaults to `./mcp_out` (set it with `-o`).

---

## 4. Options

| Short | Long              | Default                         | Meaning |
|-------|-------------------|---------------------------------|---------|
| `-u`  | `--url`           | `$MCP_URL`, or `http://127.0.0.1:8083` | MCP endpoint. Use `http://127.0.0.1:8082/mcp` to skip the proxy. |
| `-H`  | `--header`        | none                            | Extra header `'Name: value'`. Repeatable. Names are case-insensitive. Overrides `-A`. |
| `-A`  | `--agent-headers` | off                             | Send the five `x-*` context headers from the agent config. |
| `-o`  | `--out`           | `mcp_out`                       | Output directory for file modes. |
| `-t`  | `--timeout`       | `60`                            | Seconds allowed per HTTP request. |
| `-a`  | `--args`          | `{}`                            | `call` only: arguments as inline JSON, `@file.json`, or `-` (stdin). Must be a JSON object. |
| `-e`  | `--envelope`      | off                             | `call` only: print the full JSON-RPC response. |
| `-R`  | `--no-redact`     | off                             | `call` only: turn off credential masking. |
| `-h`  | `--help`          |                                 | Show help. |

---

## 5. Environment variables

| Variable          | Effect |
|-------------------|--------|
| `MCP_URL`         | Default endpoint when `-u` isn't given. |
| `OPENCODE_CONFIG` | Config file that `-A` reads. The runtime sets it on every run. Fallback: `/tmp/vega-home-vega/.vega/opencode.json`. |

```bash
export MCP_URL=http://127.0.0.1:8082/mcp   # always use the direct server
python3 mcp_tools.py n
```

---

## 6. Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Success. A "not found" answer (for example `get_flag` returning `status: not_found`) is still success. |
| `1`  | The tool reported a failure: `isError` set, `status: "error"`, or a non-empty `"error"` field. Unknown tool names also end up here. |
| `2`  | Usage error, network or HTTP error, malformed response, or a JSON-RPC protocol error. |
| `130`| Interrupted with Ctrl-C. |

Data goes to **stdout**. Status lines and errors go to **stderr**, so piping
into `python3`/`jq` stays clean.

---

## 7. Context headers and `-A`

The agent sends five headers on every MCP request. `-A` reads them from the
current run's config and sends them too.

| Header              | Needed by | Without it |
|---------------------|-----------|------------|
| `x-project-id`      | `logs`, `traces`, `error_groups`, `sessions`, `metrics`, `get_keys`, dashboards, `list_alerts`, `jira_projects`, GitHub tools | `Missing X-Project-Id header`, or an empty list with `"error": "no project context"` |
| `x-permission-mode` | GitHub tools | `not allowed in request mode ''` |
| `x-conversation-id` | `get_conversation_link`, `tag_conversation`, `untag_conversation` | `missing_conversation_id` |
| `x-app-base-url`    | Tools that return links | Links (for example `alert_url`) come back `null` |
| `x-ld-project-key`  | Optional | LaunchDarkly tools use project `default` |

**Works with no headers:** `launchdarkly_environments`, `launchdarkly_list_flags`,
`launchdarkly_get_flag`, `launchdarkly_list_metrics`, `launchdarkly_audit_log`,
`match_release_policies`.

**Rule of thumb:** if you're not sure, add `-A`.

Header values change from run to run. Don't save them; let `-A` read them fresh each time.

---

## 8. Practical examples

### 8.1 Explore the tool list

```bash
# All tool names
python3 mcp_tools.py n

# Only the GitHub-related tools
python3 mcp_tools.py n 2>/dev/null | grep github

# Count the tools
python3 mcp_tools.py n 2>/dev/null | wc -l

# Full definition of one tool
python3 mcp_tools.py t launchdarkly_get_flag

# Just the input schema of a tool
python3 mcp_tools.py t launchdarkly_get_flag 2>/dev/null \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['inputSchema'], indent=2))"

# Required parameters of a tool
python3 mcp_tools.py t logs 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['inputSchema'].get('required', []))"
```

### 8.2 Export the tool list to files

```bash
# Everything, written to ./mcp_out
python3 mcp_tools.py a

# Everything, written to ./dump
python3 mcp_tools.py -o dump a

# Exact server response only
python3 mcp_tools.py r

# Schemas without the long descriptions
python3 mcp_tools.py nd
python3 mcp_tools.py s

# One file per tool
python3 mcp_tools.py pt
ls mcp_out/tools/

# Split into parts of about 30 KB (old part files are removed first)
python3 mcp_tools.py ch 30000
ls mcp_out/part*.json
```

### 8.3 LaunchDarkly tools (no headers needed)

```bash
# Environments in project "default"
python3 mcp_tools.py c launchdarkly_environments

# First 5 flags
python3 mcp_tools.py c launchdarkly_list_flags -a '{"limit": 5}'

# One flag, including its on/off state in production
python3 mcp_tools.py c launchdarkly_get_flag \
  -a '{"flag_key": "my-flag", "environment_key": "production"}'

# Targeting rules for a flag
python3 mcp_tools.py c launchdarkly_get_flag_targeting \
  -a '{"flag_key": "my-flag", "environment_key": "production"}'

# Last 5 audit-log entries
python3 mcp_tools.py c launchdarkly_audit_log -a '{"limit": 5}'

# Flag keys only
python3 mcp_tools.py c launchdarkly_list_flags -a '{"limit": 50}' 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('\n'.join(f.get('key','') for f in d.get('flags', [])))"
```

> The shape of each payload depends on the tool. Run the call once without a
> filter to see the field names before you write a parser.

### 8.4 Observability data (needs `-A`)

```bash
# 10 most recent error logs since midnight UTC
python3 mcp_tools.py -A c logs \
  -a '{"start_date": "2026-10-21T00:00:00Z", "query": "level=error", "limit": 10}'

# Error groups in the last day
python3 mcp_tools.py -A c error_groups \
  -a '{"start_date": "2026-10-20T15:00:00Z", "count": 20}'

# Slow traces (over 1 second)
python3 mcp_tools.py -A c traces \
  -a '{"start_date": "2026-10-21T12:00:00Z", "query": "duration>1s", "limit": 20}'

# Error count grouped by service
python3 mcp_tools.py -A c metrics \
  -a '{"product_type": "errors", "start_date": "2026-10-20T00:00:00Z", "group_by": "service_name"}'

# Which log attribute keys exist
python3 mcp_tools.py -A c get_keys \
  -a '{"product_type": "logs", "start_date": "2026-10-20T00:00:00Z", "limit": 50}'

# Which services exist
python3 mcp_tools.py -A c get_values \
  -a '{"product_type": "traces", "key_name": "service_name", "start_date": "2026-10-20T00:00:00Z"}'

# Existing alerts and dashboards
python3 mcp_tools.py -A c list_alerts
python3 mcp_tools.py -A c list_dashboards -a '{"limit": 20}'
```

### 8.5 GitHub tools (needs `-A`)

```bash
# All repositories the GitHub app can access
python3 mcp_tools.py -A c github_repositories

# Repository names only
python3 mcp_tools.py -A c github_repositories 2>/dev/null \
  | python3 -c "import sys,json; print('\n'.join(r['full_name'] for r in json.load(sys.stdin)['repositories']))"

# Open PRs in Hritik00s/coffer
python3 mcp_tools.py -A c search_github_pull_requests \
  -a '{"repo_owner": "Hritik00s", "repo_name": "coffer", "state": "open"}'

# Code search inside one repo
python3 mcp_tools.py -A c github_code_search \
  -a '{"query": "repo:Hritik00s/coffer launchdarkly", "max_results": 10}'

# Comments and CI checks on PR #12
python3 mcp_tools.py -A c github_pull_request_comments \
  -a '{"repo_owner": "Hritik00s", "repo_name": "coffer", "pr_number": 12}'
python3 mcp_tools.py -A c get_github_pr_checks \
  -a '{"repo_owner": "Hritik00s", "repo_name": "coffer", "pr_number": 12}'
```

### 8.6 Different ways to pass arguments

```bash
# 1. Inline JSON (single quotes keep the shell from touching it)
python3 mcp_tools.py c launchdarkly_get_flag -a '{"flag_key": "my-flag"}'

# 2. From a file
cat > args.json <<'EOF'
{
  "flag_key": "my-flag",
  "environment_key": "production"
}
EOF
python3 mcp_tools.py c launchdarkly_get_flag -a @args.json

# 3. From stdin
echo '{"flag_key": "my-flag"}' | python3 mcp_tools.py c launchdarkly_get_flag -a -

# 4. Built from shell variables (the heredoc expands variables)
FLAG=my-flag; ENV=production
python3 mcp_tools.py c launchdarkly_get_flag -a - <<EOF
{"flag_key": "$FLAG", "environment_key": "$ENV"}
EOF
```

### 8.7 Headers by hand instead of `-A`

```bash
CFG="${OPENCODE_CONFIG:-/tmp/vega-home-vega/.vega/opencode.json}"
PID=$(python3 -c "import json;print(json.load(open('$CFG'))['mcp']['observability']['headers']['x-project-id'])")

python3 mcp_tools.py \
  -H 'x-permission-mode: read-write' \
  -H "x-project-id: $PID" \
  c github_repositories

# Use -A but override one value (-H wins)
python3 mcp_tools.py -A -H 'x-ld-project-key: other-project' c launchdarkly_environments
```

### 8.8 Debugging a call

```bash
# Full JSON-RPC response (content[], structuredContent, isError)
python3 mcp_tools.py -A -e c list_alerts

# Exit code of a call
python3 mcp_tools.py c list_alerts > /dev/null; echo "exit=$?"     # 1: no project context
python3 mcp_tools.py -A c list_alerts > /dev/null; echo "exit=$?"  # 0

# Longer timeout for heavy queries
python3 mcp_tools.py -t 180 -A c metrics \
  -a '{"product_type": "traces", "start_date": "2026-10-14T00:00:00Z", "group_by": "service_name"}'

# Compare the proxy and the direct server
diff <(python3 mcp_tools.py n 2>/dev/null) \
     <(python3 mcp_tools.py -u http://127.0.0.1:8082/mcp n 2>/dev/null) && echo identical
```

### 8.9 Without the script (plain curl equivalent)

```bash
curl -s -X POST http://127.0.0.1:8083 \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"launchdarkly_environments","arguments":{}}}' \
  | sed -n 's/^data: //p'
```

---

## 9. Using it in shell scripts

### 9.1 Branch on the exit code

```bash
#!/usr/bin/env bash
python3 mcp_tools.py -A c github_repositories > repos.json
case $? in
  0) echo "ok: $(python3 -c "import json;print(json.load(open('repos.json'))['total_repositories'])") repos" ;;
  1) echo "tool failed; details in repos.json" ;;
  2) echo "usage or network error; see stderr" ;;
esac
```

### 9.2 Look up several flags

```bash
#!/usr/bin/env bash
set -u
for f in flag-a flag-b flag-c; do
  out=$(python3 mcp_tools.py c launchdarkly_get_flag \
        -a "{\"flag_key\": \"$f\", \"environment_key\": \"production\"}" 2>/dev/null)
  rc=$?
  status=$(printf '%s' "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  echo "$f -> exit=$rc status=$status"
done
```

### 9.3 Daily snapshot of the tool list

```bash
#!/usr/bin/env bash
DIR="snapshots/$(date -u +%Y-%m-%d)"
python3 mcp_tools.py -o "$DIR" a || { echo "snapshot failed"; exit 1; }
echo "saved to $DIR"
```

### 9.4 Use the output from Python

```python
import json, subprocess

def mcp_call(tool, args=None, agent_headers=True):
    cmd = ["python3", "mcp_tools.py"]
    if agent_headers:
        cmd.append("-A")
    cmd += ["c", tool, "-a", json.dumps(args or {})]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 2:
        raise RuntimeError(p.stderr.strip())
    return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None

rc, repos = mcp_call("github_repositories")
if rc == 0:
    print([r["full_name"] for r in repos["repositories"]])
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `error: cannot reach http://127.0.0.1:8083: [Errno 111] Connection refused` | Not inside the agent runtime, or wrong port | Run it inside the runtime; check `-u` / `$MCP_URL` |
| `error: HTTP 404 ...` | Wrong path (`8083/mcp` or `8082` without `/mcp`) | Use `http://127.0.0.1:8083` or `http://127.0.0.1:8082/mcp` |
| `Missing X-Project-Id header` (exit 1) | Observability or GitHub tool called without headers | Add `-A` |
| `not allowed in request mode ''` (exit 1) | GitHub tool without `x-permission-mode` | Add `-A` |
| `"error": "no project context"` (exit 1) | `list_alerts` / `jira_projects` without headers | Add `-A` |
| `missing_conversation_id` | Conversation tool without `x-conversation-id` | Add `-A` |
| `error: cannot read headers from ...` | `$OPENCODE_CONFIG` points to a missing or bad file | `echo $OPENCODE_CONFIG`; check the file exists |
| `error: --args is not valid JSON` | Shell quoting broke the JSON | Wrap in single quotes, or use `@file.json` |
| `error: --args must be a JSON object` | Passed a list or a scalar | Wrap the arguments in `{...}` |
| `Unknown tool: 'x'` (exit 1) | Typo in the tool name | `python3 mcp_tools.py n` to see valid names |
| Validation error from the server (exit 1) | Missing a required argument | `python3 mcp_tools.py t <tool>` and check `inputSchema.required` |
| `error: request ... failed: timed out` | Heavy query | Raise `-t`, or narrow the time range |
| `alert_url` is `null` | `x-app-base-url` not sent | Add `-A` |
| Empty results but exit 0 | Filter matched nothing (the query language drops bad clauses silently) | Check key names with `get_keys` / `get_values` |

---

## 11. How it works

- **Transport:** a single JSON-RPC 2.0 POST for each request, using Streamable HTTP. The
  `Accept` header lists both `application/json` and `text/event-stream`. The server
  issues no session ID, so no `initialize` handshake is needed.
- **SSE parsing:** it groups `data:` lines into events, skips notifications, and
  returns the message whose `id` matches the request. Plain JSON responses work too.
- **Pagination:** it follows `nextCursor` and stops if a cursor repeats. Today the
  server returns all tools in one page, so `raw` writes the exact bytes received.
- **Unwrapping:** it prefers `structuredContent`. Otherwise it parses `content[].text`
  as JSON, and falls back to the raw text.
- **Masking:** inside every string value it masks `user:token@host`, `Bearer …`,
  `x-access-token:…`, `?token=` and `?access_token=`. The JSON structure is never changed.
- **Chunking:** each part is measured exactly as it will be written. A part goes over
  the limit only when a single tool is bigger than the limit on its own.

---

## 12. Safety notes

- `-A` gives the script the **same project, conversation and read-write
  access as the agent**. Be careful with tools that write: `create_github_pull_request`,
  `create_github_pr_comment`, `launchdarkly_create_flag`, `create_alert`,
  `create_automated_rollout_config`, `tag_conversation`, `create_jira_issue`, and similar.
- Header values identify this project and conversation. Don't paste them
  into tickets, chats or logs.
- `-R` turns off masking and can expose clone tokens from `github_repositories`.
- Files under `/tmp` don't survive between runs. Keep the script and any
  exports somewhere else if you need them later.
