# MCP transports

The official MCP Python SDK serves Streamable HTTP at
`http://127.0.0.1:<port>/mcp` on the existing application listener:

```text
python -m app serve --port 8000
```

Health, build identity, the web app, and existing APIs stay on that same port.
There is no redirect required at `/mcp`. HTTP uses stateless streaming responses;
resumable sessions and server-initiated interactions are not provided.
Disconnecting an HTTP request cancels its in-flight tool work.
The SDK's loopback Host/Origin validation and request limits remain enabled.

For Claude Code, put an HTTP entry in your MCP configuration:

```json
{
  "mcpServers": {
    "team-knowledge": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Alternatively, launch the stdio server with the environment's Python executable:

```json
{
  "mcpServers": {
    "team-knowledge": {
      "type": "stdio",
      "command": "/absolute/path/to/checkout/.venv/bin/python",
      "args": ["-m", "app", "mcp"],
      "env": {"TKA_MODELS": "fake"}
    }
  }
}
```

On Windows use the absolute path to `.venv/Scripts/python.exe`. Install dependencies
with `uv sync --frozen` and launch Claude Code from the checkout root so `app` is
importable. The exact CLI is `python -m app mcp`; it accepts no port and opens no
HTTP listener. Configuration comes from the same environment variables as the web
app, including `DATABASE_URL` and `TKA_DATA_DIR`. Real model mode requires its
environment-only OpenRouter credential. Fake mode requires no credential or network
model.

## Tools

Both transports list exactly these four tools (`app/mcp_tools.py`). Each calls the
same service function as its HTTP route, so results are identical to the API: the same
citation objects in the same order. `source` is the source id (UUID) that
`/api/sources` and `list_sources` return.

| Tool | Parameters | Same as |
|------|------------|---------|
| `search` | `query` (required), `source?`, `limit?` (default 10) | `GET /api/search` |
| `ask` | `question` (required), `source?` | `POST /api/ask` (`source_ids`) |
| `fetch_source` | `source`, `path` (required), `start_line?`, `end_line?` (1-based, inclusive) | `GET /api/sources/{id}/file` |
| `list_sources` | none | `GET /api/sources` |

`ask` returns `known`, `answer` and `citations`; a question the sources cannot answer
returns `known: false` and no citations without calling the model. `fetch_source`
reads the file at the indexed commit, so a cited range returns exactly the cited
lines. Invalid parameters, path traversal, absent files and unknown sources return an
MCP tool error (`isError`); the server keeps running. Tools report "Storage is
unavailable" until startup migrations finish. The tools read only; none writes to a
repository or runs repository code.

Each invocation owns one configuration/database runtime. Startup creates the
state directory and runs the existing migrations asynchronously. Protocol
initialization and discovery remain available while storage starts or if storage
fails; HTTP health and storage APIs remain unavailable until initialization
succeeds. Failed initialization is sticky until restart. Invalid model
configuration fails before directory creation or database writes. Stdio exits
nonzero with the safe configuration message on stderr. Other fatal adapter errors
report their exception type only. Stdio stdout contains only MCP protocol frames;
diagnostics use stderr. Closing stdin ends the server. Cooperative shutdown waits
for initialization and its database connections to finish.

`app.mcp.ServiceRegistration` is a synchronous callable receiving
`MCPServer[None]` and a `Callable[[], Settings]`. Pass the same callable as
`register_services=` to `app.features.f40_mcp.install` or `app.mcp.run_stdio`.
It is called once per server, should only register services, and must not read
readiness eagerly or create unmanaged resources. Tools may call the getter later;
it raises HTTP 503 (`Storage is unavailable`) until storage is ready. Registration
errors abort construction. The default registrar registers the four tools above; test tools and helpers
are never imported by application code.
