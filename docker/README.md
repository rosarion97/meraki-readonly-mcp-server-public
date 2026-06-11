# Cisco Meraki Read-Only MCP Server (Docker)

A production-grade, **read-only** Model Context Protocol (MCP) server for the
Cisco Meraki Dashboard API v1. Packaged as a Docker container and designed to
be launched directly by an MCP client (Claude Desktop, Claude Code, or the
**Docker Desktop MCP Toolkit**) over the **stdio** transport.

> New here? Start with the [repo overview](../README.md). This is the Docker
> sibling of [`../podman/`](../podman/README.md); the `server.py` is
> byte-for-byte identical to the Podman build — only the runtime tooling differs.

> Not affiliated with or endorsed by Cisco Systems or Cisco Meraki. Use at your
> own risk.

---

## 1. Overview

This server exposes a curated set of `GET`-only tools that wrap the Meraki
Dashboard API. It is intentionally constrained to be safe in production
environments: no tool will ever issue a `POST`, `PUT`, `PATCH`, or `DELETE`
against the Meraki cloud.

**Coverage**

| Area | What you can read |
|------|-------------------|
| Organizations | Org list/detail, networks, inventory, devices, statuses, admins, licenses, alerts, API usage, configuration changes |
| Networks | Network detail, devices, clients, events, traffic, alert history & settings, topology, client overview |
| Devices | Device detail, uplink settings, clients, LLDP/CDP neighbors, management interface, loss & latency |
| Wireless (MR) | SSIDs, connection stats, channel utilization, failed connections, signal quality, data rate, per-AP radio status & settings |
| Switch (MS) | Port config, real-time port status, stacks, VLANs, access policies |
| Security Appliance (MX) | VLANs, uplink usage history, IDS/IPS events, firewall L3/L7/inbound rules, content filtering, intrusion & malware settings, ports, static routes, site-to-site VPN config and status |
| Camera (MV) | Video and Sense (analytics) settings — metadata only, **no** video streams |

For the full per-tool reference (parameters, defaults, response-size
guardrails), see [§8 of the Podman README](../podman/README.md#8-available-tools)
— the tool surface is identical.

---

## 2. Prerequisites

* **Docker Engine 24+ / Docker Desktop 4.27+** on `$PATH`.
  Verify with `docker --version`.
* For the MCP Toolkit path (§4, Option 1): **Docker Desktop** with the
  **MCP Toolkit** enabled (Settings → Beta features → *Enable Docker MCP
  Toolkit*), which provides the `docker mcp` CLI plugin.
* **A Cisco Meraki Dashboard API key.** See the official guide:
  <https://documentation.meraki.com/General_Administration/Other_Topics/Cisco_Meraki_Dashboard_API>
* (Strongly recommended) **A read-only admin** whose API key you use here.
  In the Meraki Dashboard: **Organization → Administrators → Add admin**,
  set **Organization access** to **Read-only**, then have that admin
  generate the key under **My profile → API access**.

> Even though this server enforces read-only at the application layer, scoping
> the API key itself to read-only adds a second line of defense and matches
> the principle of least privilege.

---

## 3. Build the image

```bash
cd docker
docker build -t meraki-readonly-mcp:latest .
```

The build uses `python:3.12-slim`, installs `mcp[cli]` and `requests`, copies
in `server.py`, and switches to the unprivileged user `meraki-mcp` (uid 1001).

`.env` is excluded from the build context by
[`.dockerignore`](./.dockerignore), so credentials are **never** baked into
the image — they are supplied at runtime only.

---

## 4. Configure credentials

This server requires **two** values, both validated at startup; it refuses to
launch if either is missing:

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `MERAKI_API_KEY` | **Required.** Bearer token used for every API call. | Meraki Dashboard → *My Profile → API access → Generate new API key*. |
| `MERAKI_ORG_ID` | **Required.** Pins this server instance to a single organization. Every org-scoped tool uses this value; the model cannot target a different org. | `curl -H "Authorization: Bearer $MERAKI_API_KEY" https://api.meraki.com/api/v1/organizations` and copy the `id`. |
| `MERAKI_MAX_RESPONSE_BYTES` | *Optional.* Hard cap on the JSON size of any one tool response. Default `120000` (~30k tokens). | See [§10b of the Podman README](../podman/README.md#10b-response-size--context-window-safety). |

The server reads `MERAKI_API_KEY` and `MERAKI_ORG_ID` as **plain environment
variables only**. There is no secret-file mode — a secret must be *injected
into the environment*, not mounted as a file at `/run/secrets`. Both options
below do exactly that. Pick **one**.

### Option 1 — Docker secret store via the MCP Gateway (preferred)

The Docker MCP gateway resolves secrets from Docker Desktop's encrypted store
and injects them into the server **as environment variables** at launch — no
plaintext `.env` on disk, and no GUI registration step.

**Step A — Store the secrets**

```bash
# Store each secret once (you'll be prompted for the value, or pass KEY=value):
docker mcp secret set MERAKI_API_KEY
docker mcp secret set MERAKI_ORG_ID
# Optional response-size cap:
docker mcp secret set MERAKI_MAX_RESPONSE_BYTES

# List what's stored (values are masked):
docker mcp secret ls
```

**Step B — Install the custom catalog**

The catalog tells the gateway which image to run and which secrets to inject.

```bash
mkdir -p ~/.docker/mcp/catalogs
cp docker/custom-catalog.yaml ~/.docker/mcp/catalogs/custom.yaml
```

**Step C — Enable the server in the registry**

`~/.docker/mcp/registry.yaml` lists active servers under a single top-level
`registry:` key. Add the `meraki-readonly` entry — do **not** overwrite the
file if it already exists.

```yaml
registry:
  meraki-readonly:
    catalog: custom
    enabled: true
  # ... any other servers you already had stay here
```

Connecting Claude Desktop is covered in §6 (the explicit gateway JSON block);
that block replaces the GUI "register as a custom server" step you may have
used with the Toolkit.

> Plain `docker run` has no `.env`-free secret mechanism without Swarm, and
> this server does not read file-mounted secrets (`/run/secrets`). For a
> secret-backed setup, use the gateway path above; otherwise use `.env` below.

### Option 2 — `.env` file (fallback)

```bash
cd docker
cp .env.example .env
# Edit .env and set BOTH MERAKI_API_KEY and MERAKI_ORG_ID
chmod 600 .env
```

`.env` is gitignored and excluded from the image via `.dockerignore`. It is
mounted at runtime with `--env-file`, never baked in. Use this when you don't
have Docker Desktop or just want the simplest setup.

**Why pin the org statically?** The model cannot enumerate or query an
unrelated organization the key also has access to. Org-scoped tools take no
`org_id` parameter at all. One container = one organization; run a second
container with its own secret/`.env` to serve another org.

---

## 5. Test the container manually

Using `.env` (Option 2):

```bash
docker run --rm -i --env-file .env meraki-readonly-mcp:latest
```

You can also inject the values inline for a quick check:

```bash
docker run --rm -i \
  -e MERAKI_API_KEY=YOUR_KEY -e MERAKI_ORG_ID=YOUR_ORG_ID \
  meraki-readonly-mcp:latest
```

The container starts and silently waits on stdin — correct behaviour for an
MCP stdio server. Press **Ctrl-C** to exit. If a required value is missing the
server fails fast on stderr and exits with status 1.

---

## 6. Claude Desktop configuration

Edit your Claude Desktop config file:

* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Linux:** `~/.config/Claude/claude_desktop_config.json`
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

### Using a `.env` file (Option 2)

```json
{
  "mcpServers": {
    "meraki": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/meraki-readonly-mcp-server/docker/.env",
        "meraki-readonly-mcp:latest"
      ]
    }
  }
}
```

### Using the MCP Gateway (Option 1)

Replace `<your-username>` with your macOS username (run `whoami` to check):

```json
{
  "mcpServers": {
    "mcp-toolkit-gateway": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", "/Users/<your-username>/.docker/mcp:/mcp",
        "-v", "/Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock",
        "docker/mcp-gateway:latest",
        "--catalog=/mcp/catalogs/custom.yaml",
        "--registry=/mcp/registry.yaml",
        "--transport=stdio"
      ]
    }
  }
}
```

All three bind-mounts are required:

1. **`/var/run/docker.sock`** — lets the gateway spawn the `meraki-readonly-mcp` container.
2. **`~/.docker/mcp`** — the gateway reads the catalog and registry from here.
3. **`docker-secrets-engine/engine.sock`** — the resolver socket Docker Desktop exposes for the secret store. Without it the gateway resolves your secret URLs to empty strings and `docker run -e ""` rejects the env flags, so the server never starts and only the gateway's internal admin tools show up. On Linux Docker Desktop the host path is `~/.docker/desktop/secrets-engine/engine.sock` instead; check with `find ~ -name engine.sock 2>/dev/null`.

`claude_desktop_config.json` never contains `MERAKI_API_KEY` — the gateway resolves it from Docker's secret store at request time.

> **Shortcut alternative.** `docker mcp client connect claude-desktop` (or **MCP Toolkit > Clients** in Docker Desktop) will write a similar block for you automatically. The explicit JSON above gives you control over which catalogs load and survives Docker Desktop updates that may rewrite the auto-managed entry.

Notes:

* `-i` (`--interactive`) on `docker run` is **required** — MCP stdio needs
  stdin attached.
* `--rm` cleans up the container after each session.
* Every path must be **absolute**. Claude Desktop does not expand `~` or use
  your shell's working directory.

Restart Claude Desktop after editing the config.

---

## 7. Claude Code configuration

Claude Code uses the same `mcp-toolkit-gateway` block from §6 — same `command`,
same `args` — but reads it from a different file. There are three scopes:

| Scope | File | Sharing |
|---|---|---|
| **local** (default) | `~/.claude.json`, under this project's entry | just you, just this project |
| **project** | `.mcp.json` at the project root | shared via git with collaborators |
| **user** (global) | `~/.claude.json`, top level | just you, every project |

**Easiest path — let the CLI write it for you.** Replace `<your-username>` and
pick the scope you want:

```bash
claude mcp add -s user mcp-toolkit-gateway -- \
  docker run -i --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /Users/<your-username>/.docker/mcp:/mcp \
  -v /Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock \
  docker/mcp-gateway:latest \
  --catalog=/mcp/catalogs/custom.yaml \
  --registry=/mcp/registry.yaml \
  --transport=stdio
```

Use `-s user` for global, `-s project` to commit the entry to `.mcp.json` for
collaborators, or omit `-s` for the default local scope. Everything after `--`
is the same docker invocation Claude Desktop uses — the schema is
byte-for-byte identical.

Verify with `claude mcp list`. The §4 secrets and catalog / registry setup all
carry over; nothing else changes.

### `.env` shortcut (Option 2 only)

If you set up secrets via Option 2 (plaintext `.env`) instead of the gateway,
skip the block above and use the env-file form directly. `-i` is mandatory;
paths must be absolute:

```bash
claude mcp add meraki -- \
  docker run --rm -i \
  --env-file /absolute/path/to/docker/.env \
  meraki-readonly-mcp:latest
```

---

## 7b. Codex configuration

OpenAI Codex reads MCP server config from a TOML file instead of JSON. Two
scopes:

| Scope | File | Trust requirement |
|---|---|---|
| **global** | `~/.codex/config.toml` | none |
| **project** | `.codex/config.toml` at the project root | Codex only loads project files for **trusted** projects — confirm trust in Codex before relying on this scope |

Same gateway invocation as §6, mechanically translated from JSON to TOML
(`mcpServers.foo` → `[mcp_servers.foo]`; same `command`, same `args`). Replace
`<your-username>` with your macOS username (run `whoami` to check):

```toml
[mcp_servers.mcp-toolkit-gateway]
command = "docker"
args = [
  "run",
  "-i",
  "--rm",
  "-v",
  "/var/run/docker.sock:/var/run/docker.sock",
  "-v",
  "/Users/<your-username>/.docker/mcp:/mcp",
  "-v",
  "/Users/<your-username>/Library/Caches/docker-secrets-engine/engine.sock:/root/.cache/docker-secrets-engine/engine.sock",
  "docker/mcp-gateway:latest",
  "--catalog=/mcp/catalogs/custom.yaml",
  "--registry=/mcp/registry.yaml",
  "--transport=stdio",
]
```

Restart Codex or open a new project thread so the MCP server loads. The §4
secrets and catalog / registry setup all carry over; nothing else changes.

---

## 8. Available tools, rate limiting, response-size safety

These are identical to the Podman build. See the Podman README:

* [§8 Available Tools](../podman/README.md#8-available-tools)
* [§9 Example prompts](../podman/README.md#9-example-prompts-to-use-with-claude)
* [§10 Rate Limiting](../podman/README.md#10-rate-limiting)
* [§10b Response size & context-window safety](../podman/README.md#10b-response-size--context-window-safety)

---

## 9. Security notes

* **API key handling.** Read once at process start from the environment
  (plain env vars only — no secret-file mode). It is never written to
  stdout/stderr, never echoed in a tool response, and never included in error
  strings. The key lives only in the container's process memory.
* **Secrets over `.env`.** Prefer an MCP Toolkit secret (Option 1), which
  injects the value as an env var and keeps the credential out of a plaintext
  file in your project, out of the image, and out of `docker inspect` output.
  `.env` remains supported as a fallback.
* **Never baked into the image.** `.env` is excluded from the build context by
  `.dockerignore` and is gitignored. Credentials are supplied at runtime only.
* **Single-org pin.** `MERAKI_ORG_ID` is read once at start; every org-scoped
  tool uses it directly — the model cannot target a different org.
* **Non-root runtime.** The container drops to `meraki-mcp` (uid 1001) before
  executing `server.py`.
* **No write paths.** Every tool uses `requests.Session.get`. There is no
  helper that performs `POST`, `PUT`, `PATCH`, or `DELETE` — the absence of
  those verbs is the read-only guarantee. A dedicated `attempt_write_operation`
  tool returns the canonical refusal string.
* **Recommended key scope.** Generate the API key under a dedicated Meraki
  admin whose **Organization access** is **Read-only**.

---

## 10. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Claude doesn't see the server | Confirm `docker` is in `$PATH` for the user running the client. Confirm the image exists: `docker images \| grep meraki-readonly-mcp`. |
| Exits with `MERAKI_API_KEY is not set` | `.env` missing / wrong `--env-file` path (must be absolute), or the MCP Toolkit secret isn't mapped to the `MERAKI_API_KEY` env var. |
| Exits with `MERAKI_ORG_ID is not set` | Same root causes, for the org pin. Retrieve it via the `curl` in §4. |
| `401 Unauthorized` | The API key is invalid/revoked. Re-generate under the right admin profile. |
| `403 Forbidden` | Key valid but lacks access to the resource (often a wrong `MERAKI_ORG_ID`). Compare with `get_organizations`. |
| `404 Not Found` on org calls | `MERAKI_ORG_ID` is wrong. Verify with `get_organizations`. |
| `Meraki API rate limit hit` | Wait 1–2 s and retry; batch fewer entities per turn. |
| Changed `server.py`, old behaviour persists | Rebuild without cache: `docker build --no-cache -t meraki-readonly-mcp:latest .` and restart the client. |
| stdio framing errors | Ensure `-i` is present on `docker run`. Without it Docker detaches stdin and the MCP handshake fails. |

---

## 11. Read-only invariant check

Before shipping any change to `server.py`, confirm no write verb exists:

```bash
grep -nE 'session\.(post|put|patch|delete)|_session\.(post|put|patch|delete)' server.py
# Must return no matches.
```

`docker/server.py` is kept byte-for-byte identical to `podman/server.py`.
Diff them after any edit:

```bash
diff ../podman/server.py server.py   # expect no output
```

---

## License & contributions

Provided as-is for internal use. PRs that add additional **read-only** Meraki
endpoints are welcome; any change that introduces a write-capable verb against
the Dashboard API will be rejected on sight.
