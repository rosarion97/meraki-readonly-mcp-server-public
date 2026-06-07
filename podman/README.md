# Cisco Meraki Read-Only MCP Server (Podman)

A production-grade, **read-only** Model Context Protocol (MCP) server for the
Cisco Meraki Dashboard API v1. Packaged as a Podman container and designed to
be launched directly by an MCP client (Claude Desktop or Claude Code) over the
**stdio** transport.

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

**Why read-only?**

* Safe to expose to LLM agents — accidental writes are physically impossible
  because the corresponding HTTP verbs are never used.
* Predictable blast radius for shared / production environments.
* Encourages using a dedicated read-only Meraki admin for the API key.

---

## 2. Prerequisites

* **Podman 4.x or newer** installed and on `$PATH`.
  Verify with `podman --version`.
* **A Cisco Meraki Dashboard API key.** See the official guide:
  <https://documentation.meraki.com/General_Administration/Other_Topics/Cisco_Meraki_Dashboard_API>
* (Strongly recommended) **A read-only admin** whose API key you use here.
  In the Meraki Dashboard:
  1. Go to **Organization → Administrators**.
  2. Click **Add admin**.
  3. Give the admin a descriptive name (e.g. `mcp-readonly`) and email.
  4. Set **Organization access** to **Read-only**.
  5. Save. Have that admin sign in, go to **My profile → API access →
     Generate new API key**, copy the key, and store it securely.

> Even though this server enforces read-only at the application layer, scoping
> the API key itself to read-only adds a second line of defense and matches
> the principle of least privilege.

---

## 3. Build the container

```bash
git clone <your-fork-or-repo-url>
cd meraki-readonly-mcp
podman build -t meraki-readonly-mcp:latest .
```

The build uses `python:3.12-slim`, installs `mcp[cli]` and `requests`, copies
in `server.py`, and switches to the unprivileged user `meraki-mcp` (uid 1001).

---

## 4. Configure credentials

This server requires **two** environment variables. Both are validated at
startup; the server refuses to launch if either is missing.

| Variable | Purpose | Where to get it |
|----------|---------|-----------------|
| `MERAKI_API_KEY` | **Required.** Bearer token used for every API call. | Meraki Dashboard → *My Profile → API access → Generate new API key*. |
| `MERAKI_ORG_ID` | **Required.** Pins this server instance to a single organization. Every org-scoped tool uses this value; the model cannot target a different org. | Run `curl -H "Authorization: Bearer $MERAKI_API_KEY" https://api.meraki.com/api/v1/organizations` and copy the `id` of the org you want this instance to serve. |
| `MERAKI_MAX_RESPONSE_BYTES` | *Optional.* Hard cap on the JSON size of any one tool response. Responses above this size are returned as a truncation envelope with a `_hint` field telling the model how to re-query. Default `120000` (~30k tokens). Raise for big-window models, lower for small ones. | Pick a value based on your model's context window. |

The server reads `MERAKI_API_KEY` and `MERAKI_ORG_ID` as **plain environment
variables only**. There is no secret-file mode — a secret must be *injected
into the environment*, not mounted as a file at `/run/secrets`. Both options
below do exactly that. Pick **one**.

### Option 1 — podman secret (preferred)

Store each value once in Podman's encrypted secret store; nothing sensitive
sits in a plaintext file in your project, and it never appears in
`podman inspect` output.

```bash
printf '%s' 'YOUR_MERAKI_API_KEY' | podman secret create meraki_api_key -
printf '%s' 'YOUR_ORG_ID'         | podman secret create meraki_org_id  -
podman secret ls   # confirm both exist (values are not shown)
```

Then inject each secret into the env var the server reads with
`--secret …,type=env`:

```bash
podman run --rm -i \
  --secret meraki_api_key,type=env,target=MERAKI_API_KEY \
  --secret meraki_org_id,type=env,target=MERAKI_ORG_ID \
  meraki-readonly-mcp:latest
```

> Use `type=env`, not the default `type=mount`. The server reads env vars, not
> files under `/run/secrets`.

### Option 2 — `.env` file (fallback)

```bash
cp .env.example .env
# Edit .env and set BOTH:
#   MERAKI_API_KEY=...
#   MERAKI_ORG_ID=...
chmod 600 .env   # restrict file perms
```

`.env` is gitignored and excluded from the image by `.containerignore` — it is
mounted at runtime with `--env-file`, never baked into the image. Do not
commit it.

**Why pin the org statically?**

* The model cannot accidentally enumerate or query an unrelated organization
  that the admin's API key also has access to.
* Org-scoped tools take no `org_id` parameter at all — there is no surface
  for the model to get it wrong.
* One container = one organization. Run a second container with a different
  `.env` if you need to serve a second org.

---

## 5. Test the container manually

```bash
podman run --rm \
  --env-file .env \
  meraki-readonly-mcp:latest
```

The container will start and silently wait on stdin — that is correct
behaviour for an MCP stdio server. Press **Ctrl-C** to exit.

If the API key is missing or empty, the server fails fast with a clear error
on stderr and exits with status 1.

---

## 6. Claude Desktop configuration

Edit your Claude Desktop config file:

* **macOS / Linux:** `~/.claude/claude_desktop_config.json`
  (or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS)
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add (or merge) the `meraki` entry:

```json
{
  "mcpServers": {
    "meraki": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/meraki-readonly-mcp/.env",
        "meraki-readonly-mcp:latest"
      ]
    }
  }
}
```

Using podman secrets instead of `.env` (Option 1) — swap the `--env-file`
arg for `--secret` args:

```json
{
  "mcpServers": {
    "meraki": {
      "command": "podman",
      "args": [
        "run", "--rm", "-i",
        "--secret", "meraki_api_key,type=env,target=MERAKI_API_KEY",
        "--secret", "meraki_org_id,type=env,target=MERAKI_ORG_ID",
        "meraki-readonly-mcp:latest"
      ]
    }
  }
}
```

Notes:

* `-i` (`--interactive`) is **required** — MCP stdio needs stdin attached.
* `--rm` cleans up the container after each session so you don't accumulate
  stopped containers.
* The path passed to `--env-file` **must be absolute**. Claude Desktop does
  not expand `~` or run commands from your shell's working directory.
* The secret variant needs the secrets created first (`podman secret create`,
  see §4 Option 1). The secrets live in Podman's store, so no path is needed.

Restart Claude Desktop after editing the config. The `meraki` server should
appear in the MCP tools list.

---

## 7. Claude Code configuration

Claude Code uses the same `command` / `args` schema as §6, just in a different
file. Three scopes:

| Scope | File | Sharing |
|---|---|---|
| **local** (default) | `~/.claude.json`, under this project's entry | just you, just this project |
| **project** | `.mcp.json` at the project root | shared via git with collaborators |
| **user** (global) | `~/.claude.json`, top level | just you, every project |

**Easiest path — let the CLI write it for you.** Pick the scope and option that
matches §4:

Option 1 (Podman secret store, preferred):

```bash
claude mcp add -s user meraki -- \
  podman run --rm -i \
  --secret meraki_api_key,type=env,target=MERAKI_API_KEY \
  --secret meraki_org_id,type=env,target=MERAKI_ORG_ID \
  meraki-readonly-mcp:latest
```

Option 2 (plaintext `.env` file):

```bash
claude mcp add -s user meraki -- \
  podman run --rm -i \
  --env-file /absolute/path/to/.env \
  meraki-readonly-mcp:latest
```

Use `-s user` for global, `-s project` to commit the entry to `.mcp.json` for
collaborators, or omit `-s` for the default local scope. Verify with
`claude mcp list`.

---

## 7b. Codex configuration

OpenAI Codex reads MCP server config from a TOML file instead of JSON. Two
scopes:

| Scope | File | Trust requirement |
|---|---|---|
| **global** | `~/.codex/config.toml` | none |
| **project** | `.codex/config.toml` at the project root | Codex only loads project files for **trusted** projects — confirm trust in Codex before relying on this scope |

The translation from the §6 JSON is mechanical: `mcpServers.foo` →
`[mcp_servers.foo]`; same `command`, same `args`.

Option 1 (Podman secret store):

```toml
[mcp_servers.meraki]
command = "podman"
args = [
  "run", "--rm", "-i",
  "--secret", "meraki_api_key,type=env,target=MERAKI_API_KEY",
  "--secret", "meraki_org_id,type=env,target=MERAKI_ORG_ID",
  "meraki-readonly-mcp:latest",
]
```

Option 2 (`.env` file):

```toml
[mcp_servers.meraki]
command = "podman"
args = [
  "run", "--rm", "-i",
  "--env-file", "/absolute/path/to/.env",
  "meraki-readonly-mcp:latest",
]
```

Restart Codex or open a new project thread so the MCP server loads.

---

## 8. Available Tools

> **About `verbose` and response size.** Tools marked with a `verbose` flag
> return a narrowed set of fields by default to keep responses small for the
> model's context window. Pass `verbose=True` to get the full Meraki record.
> Every tool's response is additionally capped at `MERAKI_MAX_RESPONSE_BYTES`
> — over the cap, the server returns a truncation envelope with a `_hint`
> field. See [§10b](#10b-response-size--context-window-safety).

### Organization

> All org-scoped tools are pinned to `MERAKI_ORG_ID` from the `.env` file and
> take **no** `org_id` parameter. To serve a different org, run a second
> container with a different `.env`.

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_organizations` | — | Diagnostic: lists every org the API key can see (ignores the pin). Use to verify `MERAKI_ORG_ID` matches what your key has access to. |
| `get_organization` | — | Details of the configured organization. |
| `get_organization_networks` | `verbose=False` | All networks in the configured organization. |
| `get_organization_inventory_devices` | `verbose=False` | Device inventory. |
| `get_organization_uplinks_statuses` | — | Uplink status across all MX/MG/Z. |
| `get_organization_alerts_current` | — | Current alert overview, grouped by type. |
| `get_organization_api_requests_overview` | — | API call & error counters. |
| `get_organization_admins` | — | Dashboard administrators. |
| `get_organization_licenses_overview` | — | License summary. |
| `get_organization_devices` | `verbose=False` | Every claimed device in the org. |
| `get_organization_devices_statuses` | `verbose=False` | Online/offline/alerting status per device. |
| `get_organization_devices_availabilities` | — | Reachability/availability history. |
| `get_organization_configuration_changes` | `timespan=3600`, `per_page=25` | Configuration change log. |

### Network

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_network` | `network_id` | Details of a single network. |
| `get_network_devices` | `network_id`, `verbose=False` | Devices in the network. |
| `get_network_clients` | `network_id`, `timespan=3600`, `per_page=50`, `verbose=False` | Clients seen in the timespan (sec). |
| `get_network_events` | `network_id`, `product_type="appliance"`, `per_page=25` | Network event log. |
| `get_network_traffic` | `network_id`, `timespan=3600` | Application traffic analysis. |
| `get_network_alerts_history` | `network_id` | Historical alerts. |
| `get_network_alerts_settings` | `network_id` | Alert thresholds and recipients. |
| `get_network_topology_link_layer` | `network_id` | LLDP/CDP adjacency graph. |
| `get_network_clients_overview` | `network_id`, `timespan=86400` | Aggregate client counts/usage. |
| `get_network_client` | `network_id`, `client_id` | Single client detail (by ID/MAC/IP). |

### Device

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_device` | `serial` | Device details. |
| `get_device_uplink_settings` | `serial` | Uplink configuration. |
| `get_device_clients` | `serial`, `timespan=3600`, `verbose=False` | Clients seen on the device. |
| `get_device_lldp_cdp` | `serial` | LLDP/CDP neighbors. |
| `get_device_management_interface` | `serial` | Management interface configuration. |
| `get_device_loss_and_latency_history` | `serial`, `ip="8.8.8.8"`, `timespan=3600`, `uplink="wan1"` | Probe loss & latency history. |

### Wireless (MR)

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_network_wireless_ssids` | `network_id` | All SSIDs in the network. |
| `get_network_wireless_ssid` | `network_id`, `number` | Single SSID (0–14). |
| `get_network_wireless_clients_connection_stats` | `network_id`, `timespan=3600` | Per-client connection stats. |
| `get_network_wireless_channel_utilization` | `network_id`, `timespan=3600` | Channel utilization per AP. |
| `get_network_wireless_failed_connections` | `network_id`, `timespan=3600` | Failed assoc/auth/DHCP/DNS attempts. |
| `get_network_wireless_signal_quality_history` | `network_id`, `timespan=3600` | SNR/RSSI history. |
| `get_network_wireless_data_rate_history` | `network_id`, `timespan=3600` | Aggregate data-rate history. |
| `get_device_wireless_status` | `serial` | Live radio status for an AP. |
| `get_device_wireless_radio_settings` | `serial` | Radio (channel/power) settings. |

### Switch (MS)

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_network_switch_ports_statuses` | `network_id` | Real-time port status, all switches. |
| `get_device_switch_ports` | `serial` | Port configuration for a switch. |
| `get_device_switch_port` | `serial`, `port_id` | Single-port configuration. |
| `get_device_switch_ports_statuses` | `serial` | Real-time port status, single switch. |
| `get_network_switch_stacks` | `network_id` | Configured switch stacks. |
| `get_network_switch_vlans` | `network_id` | Switch VLANs. |
| `get_network_switch_access_policies` | `network_id` | 802.1X / MAB access policies. |

### Security Appliance (MX)

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_network_appliance_vlans` | `network_id` | MX VLANs. |
| `get_network_appliance_vlan` | `network_id`, `vlan_id` | Single MX VLAN. |
| `get_network_appliance_uplinks_usage_history` | `network_id`, `timespan=3600` | WAN uplink usage history. |
| `get_network_appliance_security_events` | `network_id`, `timespan=3600`, `per_page=25` | IDS/IPS events. |
| `get_network_appliance_firewall_l3_rules` | `network_id` | Layer-3 outbound firewall rules. |
| `get_network_appliance_firewall_l7_rules` | `network_id` | Layer-7 application firewall rules. |
| `get_network_appliance_firewall_inbound_rules` | `network_id` | Inbound firewall rules. |
| `get_network_appliance_content_filtering` | `network_id` | Content-filtering categories / URL lists. |
| `get_network_appliance_security_intrusion` | `network_id` | IDS/IPS settings. |
| `get_network_appliance_security_malware` | `network_id` | AMP / anti-malware settings. |
| `get_network_appliance_ports` | `network_id` | MX LAN port configuration. |
| `get_network_appliance_static_routes` | `network_id` | Static routes on the MX. |
| `get_network_appliance_site_to_site_vpn` | `network_id` | AutoVPN site-to-site config. |
| `get_organization_appliance_vpn_statuses` | — | AutoVPN reachability per MX (configured org). |
| `get_organization_appliance_vpn_stats` | `timespan=3600` | AutoVPN loss/latency/jitter (configured org). |

### Camera (MV) — metadata only

| Tool | Parameters | Description |
|------|------------|-------------|
| `get_device_camera_video_settings` | `serial` | Video quality / retention settings. |
| `get_device_camera_sense_settings` | `serial` | MV Sense analytics settings. |

### Read-only safety

| Tool | Parameters | Description |
|------|------------|-------------|
| `attempt_write_operation` | `method="POST"`, `path="/example"` | Always returns the standard refusal string. Useful for verifying the server's read-only stance. |

---

## 9. Example prompts to use with Claude

Once the server is wired in to Claude Desktop or Claude Code, try prompts
like these:

* "List the networks in my Meraki org."
* "Give me the uplink status for every device."
* "What clients are connected to network L_123456 right now?"
* "Show me IDS security events for network L_123456 in the last 24 hours."
* "What's the channel utilization on the wireless network L_123456?"
* "List all switch port statuses for serial Q2XX-XXXX-XXXX."
* "Which clients failed to associate to wireless network L_123456 today?"
* "What L3 firewall rules are configured on network L_123456?"
* "Show me the AutoVPN reachability matrix."
* "What configuration changes happened in the last 24h?"
* "Confirm my API key is pinned to the right organization." *(triggers `get_organizations` for sanity check)*

Claude will pick the matching tool, hand it the required identifiers, and
summarise the JSON the Meraki API returns.

---

## 10. Rate Limiting

The Meraki Dashboard API enforces **10 requests per second per organization**.
When that limit is exceeded the API responds with HTTP 429, and this server
surfaces a friendly message:

> *"Meraki API rate limit hit. Retry after a moment."*

If you're asking Claude to enumerate a large estate (e.g. "give me port
status for every switch in every network"), expect to pace the requests or
to retry once or twice. The server does **not** auto-retry on your behalf —
it returns the error so the model and the user stay in control.

---

## 10b. Response size & context-window safety

Meraki endpoints such as `/networks/{id}/events`, `/networks/{id}/traffic`,
`/organizations/{id}/devices/statuses` and `/configurationChanges` can return
multi-megabyte JSON arrays. Handing that raw payload to an LLM tool call
will blow past the model's context window and lead to truncation or
hallucination. This server has three layers of protection against that:

**1. Tighter default time windows.** High-volume endpoints default to
`timespan=3600` (1 hour) instead of 24 hours. The model can always ask for
more by passing a larger value.

**2. Server-side `per_page` cap.** Every tool that accepts `per_page` is
clamped to `PER_PAGE_CAP` (default 50). The model can request less, never
more.

**3. Field projection.** High-cardinality list endpoints
(`get_organization_devices`, `get_organization_devices_statuses`,
`get_organization_inventory_devices`, `get_organization_networks`,
`get_network_devices`, `get_network_clients`, `get_device_clients`) return
a curated subset of fields by default. Pass `verbose=True` to get the full
record back.

**4. A hard byte cap on every response.** `MERAKI_MAX_RESPONSE_BYTES`
(default `120000`, ~30k tokens) caps the JSON size of any single tool
response. When a payload would exceed the cap, the server returns a
truncation envelope instead:

```jsonc
{
  "_truncated": true,
  "_returned": 42,            // items included
  "_total": 1850,             // items the API actually returned
  "_bytes_cap": 120000,
  "_hint": "Lower timespan or per_page; IDS events can be dense.",
  "data": [ /* first 42 items */ ]
}
```

The `_hint` field is the important part: the model sees that data was cut
and gets a one-line nudge on how to re-query for what it actually needs.

**Tuning.** If you're running a big-window model (Gemini 1.5 Pro,
Claude with extended context) and want to allow bigger responses, raise
the cap:

```
MERAKI_MAX_RESPONSE_BYTES=400000
```

If you're running a small-window model and getting "context full"
errors anyway, lower it:

```
MERAKI_MAX_RESPONSE_BYTES=60000
```

Restart the MCP client (which restarts the container) for the change
to take effect.

---

## 11. Security Notes

* **API key handling.** `MERAKI_API_KEY` is read once at process start from
  the container's environment. It is never written to stdout/stderr, never
  echoed back in a tool response, and never included in error strings. The
  key lives only inside the container's process memory.
* **Single-org pin.** `MERAKI_ORG_ID` is also read once at process start.
  Every org-scoped tool uses this value directly — the model cannot pass a
  different org ID even if a user asks it to. To serve a second organization,
  run a second container with its own `.env`.
* **Non-root runtime.** The container drops to `meraki-mcp` (uid 1001)
  before executing `server.py`.
* **No write paths.** Every tool uses `requests.Session.get`. There is no
  helper in the code that performs `POST`, `PUT`, `PATCH`, or `DELETE` —
  the absence of those methods is the read-only guarantee. A dedicated
  `attempt_write_operation` tool returns the canonical refusal string so
  the model can explain refusals clearly to the user.
* **Recommended key scope.** Generate the API key under a dedicated Meraki
  admin whose **Organization access** is set to **Read-only** in the
  Dashboard. This adds defense in depth.
* **Secret storage.** Prefer a `podman secret` injected with `type=env`
  (§4 Option 1) over `.env`: it keeps the key out of a plaintext project file,
  out of the image, and out of `podman inspect` output. The server reads
  credentials as plain environment variables only. `.env` is gitignored and
  excluded from the build context by `.containerignore`; if you use it, don't
  commit it. If you rotate keys, restart the MCP client so it relaunches the
  container with the new credentials.

---

## 12. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `Connection refused` / Claude doesn't see the server | Confirm `podman` is in `$PATH` for the user that runs Claude Desktop / Claude Code. Confirm the image exists: `podman images \| grep meraki-readonly-mcp`. |
| Server exits immediately with `MERAKI_API_KEY ... is not set` | Either the `.env` file is missing, the path in `--env-file` is wrong, or the variable is empty. Path must be **absolute**. |
| Server exits immediately with `MERAKI_ORG_ID ... is not set` | Same root causes as above, but for the org pin. Run `curl -H "Authorization: Bearer $MERAKI_API_KEY" https://api.meraki.com/api/v1/organizations` to retrieve the ID, then put it in `.env`. |
| `401 Unauthorized` | The API key in `.env` is invalid, revoked, or belongs to no organization. Re-generate it under the right admin profile. |
| `403 Forbidden` | The key is valid but the admin it belongs to lacks access to the resource you asked for. Often means the configured `MERAKI_ORG_ID` is for an org this admin can't see — call `get_organizations` to compare. |
| `404 Not Found` on org-scoped calls | `MERAKI_ORG_ID` is wrong (typo, deleted org, or org from a different account). Verify with `get_organizations`. |
| `404 Not Found` on network/device calls | `network_id` or `serial` is wrong. List the parents first (`get_organization_networks`, `get_organization_devices`) to find the correct ID. |
| `Meraki API rate limit hit` | Wait 1–2 seconds and retry. If it happens often, batch fewer entities per turn. |
| Changed `server.py` but Claude still sees the old behaviour | The container caches the previous image. Rebuild without cache: `podman build --no-cache -t meraki-readonly-mcp:latest .` and restart the MCP client. |
| stdio framing errors on the client | Make sure the args list contains `-i` (interactive). Without it Podman closes stdin and the MCP handshake fails. |

---

## License & contributions

This project is provided as-is for internal use. PRs that add additional
**read-only** Meraki endpoints are welcome; any change that would introduce
a write-capable verb against the Dashboard API will be rejected on sight.
