#!/usr/bin/env python3
"""Fetch and slice the MCP tools/list response, or call a tool. Standard library only.

Exit codes: 0 success, 1 the tool reported a failure, 2 usage/transport/protocol error.
"""
import argparse, json, os, re, sys, urllib.request, urllib.error

DEFAULT_URL = "http://127.0.0.1:8083"   # or http://127.0.0.1:8082/mcp
AGENT_CONFIG = os.environ.get("OPENCODE_CONFIG") or "/tmp/vega-home-vega/.vega/opencode.json"

MODES = {  # canonical name -> short aliases
    "raw": ["r"], "pretty": ["p"], "names": ["n", "ls"], "tool": ["t"],
    "nodesc": ["nd"], "schemas": ["s"], "per-tool": ["pt"], "chunks": ["ch"],
    "all": ["a"], "call": ["c"],
}
ALIAS = {alias: name for name, aliases in MODES.items() for alias in [name, *aliases]}


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def rpc(url, method, params=None, headers=None, req_id=1, timeout=60):
    """Send one JSON-RPC request; return (parsed_message, raw_json_text)."""
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    h = {"content-type": "application/json",
         "accept": "application/json, text/event-stream"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            text = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        die(f"cannot reach {url}: {e.reason}")
    except (TimeoutError, OSError) as e:
        die(f"request to {url} failed: {e}")

    if "text/event-stream" not in ctype:
        try:
            return json.loads(text), text
        except json.JSONDecodeError:
            die(f"non-JSON response ({ctype or 'no content-type'}): {text[:300]}")
    # SSE: an event's payload may span several "data:" lines; a blank line ends the event.
    buf = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
        elif line == "" and buf:
            raw = "\n".join(buf); buf = []
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == req_id:   # skip notifications
                return msg, raw
    die("no JSON-RPC response with a matching id in the SSE stream")


def fetch_tools(url, headers, timeout):
    """Full tools/list, following nextCursor. Returns (response, raw_first_page, pages)."""
    tools, cursor, seen, first_raw, page = [], None, set(), None, 0
    while True:
        page += 1
        resp, raw = rpc(url, "tools/list", {"cursor": cursor} if cursor else None,
                        headers, req_id=page, timeout=timeout)
        if "error" in resp:
            die(f"tools/list error: {json.dumps(resp['error'])}")
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            die(f"unexpected tools/list response: {raw[:300]}")
        if first_raw is None:
            first_raw = raw
        tools += result["tools"]
        cursor = result.get("nextCursor")
        if not cursor:
            break
        if cursor in seen:
            die(f"server repeated cursor {cursor!r}; stopping to avoid a loop")
        seen.add(cursor)
    return {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}, first_raw, page


def unwrap(result):
    """Turn a tools/call result into the tool's own payload."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return joined


_SECRETS = [
    (re.compile(r'(https?://)[^/@\s]+@'), r'\1<redacted>@'),                    # user:token@host
    (re.compile(r'(?i)(bearer\s+|x-access-token:)[A-Za-z0-9_\-.~+/=]+'), r'\1<redacted>'),
    (re.compile(r'(?i)([?&](?:access_)?token=)[^&\s]+'), r'\1<redacted>'),       # ?token=...
]


def redact(obj):
    """Mask credentials inside every string value (structure is never touched)."""
    if isinstance(obj, str):
        for pat, rep in _SECRETS:
            obj = pat.sub(rep, obj)
        return obj
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj


def parse_headers(items):
    out = {}
    for h in items:
        k, sep, v = h.partition(":")
        if not sep or not k.strip():
            die(f"bad header {h!r}; expected 'Name: value'")
        out[k.strip().lower()] = v.strip()
    return out


def agent_headers():
    try:
        with open(AGENT_CONFIG, encoding="utf-8") as f:
            hdrs = json.load(f)["mcp"]["observability"].get("headers") or {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
        die(f"cannot read headers from {AGENT_CONFIG}: {e}")
    return {str(k).lower(): str(v) for k, v in hdrs.items()}


def load_args(s):
    """--args value: inline JSON, @file.json, or - for stdin. Must be a JSON object."""
    try:
        if s == "-":
            s = sys.stdin.read()
        elif s.startswith("@"):
            with open(s[1:], encoding="utf-8") as f:
                s = f.read()
        val = json.loads(s)
    except OSError as e:
        die(f"cannot read --args file: {e}")
    except json.JSONDecodeError as e:
        die(f"--args is not valid JSON: {e}")
    if not isinstance(val, dict):
        die("--args must be a JSON object, e.g. '{\"flag_key\": \"x\"}'")
    return val


def write(path, obj, pretty=True):
    with open(path, "w", encoding="utf-8") as f:
        if pretty:
            json.dump(obj, f, indent=2, ensure_ascii=False)
        else:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    print(f"wrote {path} ({os.path.getsize(path):,} bytes)", file=sys.stderr)


def main():
    mode_help = ", ".join(f"{m} ({'/'.join(a)})" for m, a in MODES.items())
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-u", "--url", default=os.environ.get("MCP_URL", DEFAULT_URL),
                   help=f"MCP endpoint (default {DEFAULT_URL}, or $MCP_URL)")
    p.add_argument("-H", "--header", action="append", default=[],
                   help="extra header 'Name: value' (repeatable; overrides -A values)")
    p.add_argument("-A", "--agent-headers", action="store_true",
                   help=f"send the x-* context headers from {AGENT_CONFIG}")
    p.add_argument("-o", "--out", default="mcp_out", help="output directory for file modes")
    p.add_argument("-t", "--timeout", type=float, default=60, help="seconds per request (default 60)")
    p.add_argument("-a", "--args", default="{}",
                   help="call: tool arguments as JSON, @file.json, or - for stdin")
    p.add_argument("-e", "--envelope", action="store_true",
                   help="call: print the full JSON-RPC response, not the unwrapped payload")
    p.add_argument("-R", "--no-redact", action="store_true", help="call: do not mask credentials")
    p.add_argument("mode", metavar="MODE", help=f"one of: {mode_help}")
    p.add_argument("arg", nargs="?", help="tool name (tool/call) or chunk size in bytes (chunks)")
    a = p.parse_args()

    mode = ALIAS.get(a.mode)
    if mode is None:
        p.error(f"unknown mode {a.mode!r}; choose from: {mode_help}")
    if a.timeout <= 0:
        p.error("--timeout must be positive")

    headers = agent_headers() if a.agent_headers else {}
    headers.update(parse_headers(a.header))

    if mode == "call":
        if not a.arg:
            p.error("call needs a tool name, e.g.: call github_repositories")
        args = load_args(a.args)
        resp, raw = rpc(a.url, "tools/call", {"name": a.arg, "arguments": args},
                        headers, timeout=a.timeout)
        if "error" in resp:                          # protocol-level error
            print(json.dumps(resp["error"], indent=2, ensure_ascii=False), file=sys.stderr)
            sys.exit(2)
        result = resp.get("result")
        if not isinstance(result, dict):
            die(f"unexpected tools/call response: {raw[:300]}")
        payload = unwrap(result)
        out = resp if a.envelope else payload
        print(json.dumps(out if a.no_redact else redact(out), indent=2, ensure_ascii=False))
        # Tool-level failure: isError, status=error, or a non-empty "error" field
        # (e.g. list_alerts returns {"alerts": [], "error": "no project context"}).
        failed = bool(result.get("isError")) or (
            isinstance(payload, dict)
            and (payload.get("status") == "error" or bool(payload.get("error"))))
        sys.exit(1 if failed else 0)

    limit = 45000
    if mode == "chunks" and a.arg:
        try:
            limit = int(a.arg)
        except ValueError:
            p.error("chunk size must be an integer number of bytes")
        if limit <= 0:
            p.error("chunk size must be positive")
    if mode == "tool" and not a.arg:
        p.error("tool needs a tool name, e.g.: tool launchdarkly_get_flag")

    data, first_raw, pages = fetch_tools(a.url, headers, a.timeout)
    tools = data["result"]["tools"]
    print(f"{len(tools)} tools from {a.url}", file=sys.stderr)
    by_name = {t.get("name"): t for t in tools}

    if mode == "names":
        print("\n".join(sorted(n for n in by_name if n)))
        return
    if mode == "tool":
        if a.arg not in by_name:
            die(f"unknown tool {a.arg!r}; run 'names' to list them")
        print(json.dumps(by_name[a.arg], indent=2, ensure_ascii=False))
        return

    os.makedirs(a.out, exist_ok=True)
    if mode in ("raw", "all"):
        path = os.path.join(a.out, "tools_raw.json")
        if pages == 1:                               # exact bytes the server sent
            with open(path, "w", encoding="utf-8") as f:
                f.write(first_raw)
            print(f"wrote {path} ({os.path.getsize(path):,} bytes, verbatim)", file=sys.stderr)
        else:                                        # merged pages, so not verbatim
            write(path, data, pretty=False)
    if mode in ("pretty", "all"):
        write(os.path.join(a.out, "tools_raw_pretty.json"), data)
    if mode in ("nodesc", "all"):
        write(os.path.join(a.out, "tools_nodesc.json"),
              {**data, "result": {"tools": [{k: v for k, v in t.items() if k != "description"}
                                            for t in tools]}})
    if mode in ("schemas", "all"):
        write(os.path.join(a.out, "tools_schemas.json"),
              [{"name": t.get("name"), "inputSchema": t.get("inputSchema")} for t in tools])
    if mode in ("per-tool", "all"):
        d = os.path.join(a.out, "tools")
        os.makedirs(d, exist_ok=True)
        for t in tools:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(t.get("name") or "unnamed"))
            with open(os.path.join(d, f"{safe}.json"), "w", encoding="utf-8") as f:
                json.dump(t, f, indent=2, ensure_ascii=False)
        print(f"wrote {len(tools)} files to {d}/", file=sys.stderr)
    if mode in ("chunks", "all"):
        def nbytes(lst):                             # exact size the file will have
            return len(json.dumps(lst, indent=2, ensure_ascii=False).encode("utf-8"))
        parts, cur = [], []
        for t in tools:
            if cur and nbytes(cur + [t]) > limit:    # a single oversized tool still gets its own part
                parts.append(cur); cur = []
            cur.append(t)
        if cur:
            parts.append(cur)
        for old in os.listdir(a.out):                # drop stale parts from earlier runs
            if re.fullmatch(r"part\d+\.json", old):
                os.remove(os.path.join(a.out, old))
        for i, part in enumerate(parts, 1):
            write(os.path.join(a.out, f"part{i}.json"), part)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        main()
        sys.stdout.flush()
    except BrokenPipeError:                          # e.g. piped into head
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
