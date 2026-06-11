# meraki-readonly-mcp-server

A production-grade, **read-only** Model Context Protocol (MCP) server for the
Cisco Meraki Dashboard API v1. Designed to be launched directly by an MCP
client (Claude Desktop, Claude Code, Gemini CLI, etc.) over the **stdio**
transport.

> Not affiliated with or endorsed by Cisco Systems or Cisco Meraki. Use at your
> own risk. **Read-only is not the same as harmless** — client lists, event
> logs, and firewall rules can contain sensitive operational data; scope the
> API key accordingly.

## Container backends

This repository ships a containerised build for both Podman and Docker. They
share a byte-for-byte identical `server.py`; only the runtime tooling differs.

| Backend | Status | Folder |
|---------|--------|--------|
| Podman | stable | [`podman/`](./podman) |
| Docker | stable | [`docker/`](./docker) |

See [`podman/README.md`](./podman/README.md) or
[`docker/README.md`](./docker/README.md) for full build, configuration, and
Claude Desktop / Claude Code integration steps. The Docker guide also covers
the **Docker Desktop MCP Toolkit**.

### Credentials: secret first, `.env` fallback

The server reads `MERAKI_API_KEY` and `MERAKI_ORG_ID` as **plain environment
variables only** (no secret-file mode). Both backends prefer a container
secret *injected as an env var* over a plaintext `.env`:

* **Podman:** `podman secret create` + `--secret <name>,type=env` (see
  [podman §4](./podman/README.md#4-configure-credentials)).
* **Docker:** Docker Desktop MCP Toolkit (`docker mcp secret set`), which
  injects the secret as an env var (see
  [docker §4](./docker/README.md#4-configure-credentials)).
* **Either:** a gitignored `.env` mounted with `--env-file` as the fallback.

`.env` is gitignored and excluded from both image builds (`.dockerignore` /
`.containerignore`), so credentials are never baked into an image.

## Hard read-only guarantee

Every tool maps to an HTTP `GET` against `https://api.meraki.com/api/v1`.
The server contains no code path that issues `POST`, `PUT`, `PATCH`, or
`DELETE`, so it is incapable of modifying any Meraki configuration regardless
of what an LLM or user asks for. See
[§11 of the podman README](./podman/README.md) for the full security model.

## Coverage at a glance

* Organizations — networks, inventory, devices, statuses, admins, licenses,
  alerts, API usage, configuration changes
* Networks — devices, clients, events, traffic, alerts, topology
* Devices — detail, uplink settings, clients, LLDP/CDP, loss & latency
* Wireless (MR) — SSIDs, channel utilization, signal quality, failed
  connections, per-AP radio status
* Switch (MS) — port config + real-time port status, stacks, VLANs, access
  policies
* Security Appliance (MX) — VLANs, uplink usage, IDS/IPS events, L3/L7/
  inbound firewall rules, content filtering, intrusion & malware settings,
  static routes, AutoVPN configuration & status
* Camera (MV) — metadata only (video and Sense settings; no streams)

## Configuration in three variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `MERAKI_API_KEY` | yes | Bearer token used for every API call. |
| `MERAKI_ORG_ID` | yes | Pins this server instance to a single org. |
| `MERAKI_MAX_RESPONSE_BYTES` | no | Caps the JSON size of any single tool response. Default `120000` (~30k tokens). |

See [`podman/.env.example`](./podman/.env.example) and the podman README for
details.

## Repository layout

```
.
├── README.md      # you are here — overview + backend chooser
├── docker/        # Docker variant + custom-catalog.yaml (Docker MCP Toolkit)
└── podman/        # Podman variant, rootless
```

`docker/server.py` and `podman/server.py` are kept byte-identical
(`diff -q docker/server.py podman/server.py`).

## License

Provided as-is for internal use. Not affiliated with or endorsed by Cisco
Systems or Cisco Meraki. Pull requests that add additional
**read-only** Meraki endpoints are welcome; any change that introduces a
write-capable verb against the Dashboard API will be rejected.
