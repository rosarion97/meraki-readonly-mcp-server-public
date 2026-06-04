# Cisco Meraki Read-Only MCP Server (Docker)

A production-grade, **read-only** Model Context Protocol (MCP) server for the
Cisco Meraki Dashboard API v1. Packaged as a Docker container and designed to
be launched directly by an MCP client (Claude Desktop, Claude Code, or the
**Docker Desktop MCP Toolkit**) over the **stdio** transport.

This is the Docker sibling of [`../podman`](../podman). The `server.py` is
byte-for-byte identical to the Podman build; only the runtime tooling differs.

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

### Option 1 — Docker secret via the Docker Desktop MCP Toolkit (preferred)

The MCP Toolkit keeps secrets in Docker Desktop's encrypted store instead of a
plaintext `.env` on disk, and injects them into the server **as environment
variables** at launch.

```bash
# Store each secret once (you'll be prompted for the value, or pass KEY=value):
docker mcp secret set MERAKI_API_KEY
docker mcp secret set MERAKI_ORG_ID

# List what's stored (values are masked):
docker mcp secret ls
```

Then register this image as a custom server in the MCP Toolkit and map those
two secrets to the `MERAKI_API_KEY` / `MERAKI_ORG_ID` environment variables of
the container. The Toolkit's gateway launches the container per session and
hands the client the stdio connection — no `.env` file is involved. Connect
your client (Claude Desktop, Claude Code, etc.) to the Toolkit from the
**MCP Toolkit → Clients** tab.

> Plain `docker run` has no `.env`-free secret mechanism without Swarm, and
> this server does not read file-mounted secrets (`/run/secrets`). For a
> secret-backed setup, use the MCP Toolkit; otherwise use `.env` below.

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

### Using the MCP Toolkit (Option 1)

Connect Claude Desktop to the Toolkit from **Docker Desktop → MCP Toolkit →
Clients**; you don't hand-edit `mcpServers` for the Toolkit-managed server.

Notes:

* `-i` (`--interactive`) on `docker run` is **required** — MCP stdio needs
  stdin attached.
* `--rm` cleans up the container after each session.
* Every path must be **absolute**. Claude Desktop does not expand `~` or use
  your shell's working directory.

Restart Claude Desktop after editing the config.

---

## 7. Claude Code configuration

Project-scoped: `.claude/settings.json` at your project root. Global:
`~/.claude/settings.json`. Same shapes as §6, e.g. the `.env` form:

```json
{
  "mcpServers": {
    "meraki": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/docker/.env",
        "meraki-readonly-mcp:latest"
      ]
    }
  }
}
```

`-i` is mandatory; paths must be absolute.

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
