"""
Cisco Meraki Read-Only MCP Server
==================================
A production-grade Model Context Protocol (MCP) server that exposes
READ-ONLY access to the Cisco Meraki Dashboard API v1 over stdio transport.

HARD CONSTRAINTS
----------------
* Every tool maps to an HTTP GET against https://api.meraki.com/api/v1
* No POST, PUT, PATCH, or DELETE is ever issued. Attempts return a clear
  refusal string.
* The API key is read from MERAKI_API_KEY at startup. It is never logged
  and never returned in any tool response.
* Transport is stdio. The container is invoked directly by an MCP client
  (Claude Desktop / Claude Code).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://api.meraki.com/api/v1"
DEFAULT_TIMEOUT = 30  # seconds

# Cap the JSON size of any single tool response. Meraki endpoints like
# /events, /traffic, /devices/statuses can otherwise return megabytes of
# JSON and blow past the model's context window. Override with
# MERAKI_MAX_RESPONSE_BYTES in .env (e.g. 250000 for big-window models).
MAX_RESPONSE_BYTES = max(
    int(os.environ.get("MERAKI_MAX_RESPONSE_BYTES", "120000")),
    10_000,
)

# Hard ceiling for any tool's per_page parameter. The model can still ask
# for less; it just can't ask for more.
PER_PAGE_CAP = 50

# Credentials are read as plain environment variables only. Supply them with
# `--env-file .env`, or as a podman/docker secret injected into the env with
# `--secret <name>,type=env,target=MERAKI_API_KEY` (or the Docker Desktop MCP
# Toolkit, which injects secrets as env vars). The server does not read secret
# files mounted at /run/secrets (type=mount) — env injection is the only path.
MERAKI_API_KEY = os.environ.get("MERAKI_API_KEY", "").strip()
if not MERAKI_API_KEY:
    print(
        "ERROR: MERAKI_API_KEY is not set. Provide it as an environment "
        "variable: `--env-file .env`, or a secret injected as an env var "
        "(`--secret meraki_api_key,type=env,target=MERAKI_API_KEY`).",
        file=sys.stderr,
    )
    sys.exit(1)

MERAKI_ORG_ID = os.environ.get("MERAKI_ORG_ID", "").strip()
if not MERAKI_ORG_ID:
    print(
        "ERROR: MERAKI_ORG_ID is not set. This server is pinned to a single "
        "organization; provide it as an environment variable (`--env-file .env` "
        "or a secret injected with type=env).",
        file=sys.stderr,
    )
    sys.exit(1)

_session = requests.Session()
_session.headers.update(
    {
        "Authorization": f"Bearer {MERAKI_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Cisco-Meraki-API-Key": MERAKI_API_KEY,  # legacy header for compat
        "User-Agent": "meraki-readonly-mcp/1.0 (stdio)",
    }
)

mcp = FastMCP("meraki-readonly")


# ---------------------------------------------------------------------------
# HTTP helpers — READ-ONLY
# ---------------------------------------------------------------------------

READ_ONLY_REFUSAL = "This server is read-only. Operation refused."


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """Issue a GET to the Meraki API and return parsed JSON.

    Raises ValueError with a descriptive (but key-free) message on any
    non-200 response.
    """
    url = f"{BASE_URL}{path}"
    try:
        response = _session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise ValueError(f"Network error contacting Meraki API: {exc}") from None

    status = response.status_code
    if status == 200:
        try:
            return response.json()
        except ValueError:
            raise ValueError("Meraki API returned a non-JSON 200 response.") from None
    if status == 401:
        raise ValueError(
            "401 Unauthorized: the configured Meraki API key was rejected. "
            "Check MERAKI_API_KEY in your .env file."
        )
    if status == 403:
        raise ValueError(
            "403 Forbidden: the API key lacks permission for this resource."
        )
    if status == 404:
        raise ValueError(f"Not found: {path}")
    if status == 429:
        raise ValueError("Meraki API rate limit hit. Retry after a moment.")
    raise ValueError(f"Meraki API error {status}: {response.text[:200]}")


def _refuse_write(verb: str, path: str) -> str:
    """Helper used by the explicit write-refusal tool."""
    return f"{READ_ONLY_REFUSAL} (attempted {verb.upper()} {path})"


# ---------------------------------------------------------------------------
# Response-size guardrails
# ---------------------------------------------------------------------------


def _clamp_per_page(per_page: int, cap: int = PER_PAGE_CAP) -> int:
    """Clamp the caller's per_page request to a safe maximum."""
    try:
        n = int(per_page)
    except (TypeError, ValueError):
        return cap
    if n < 1:
        return 1
    return min(n, cap)


def _bounded(
    payload: Any,
    hint: str = (
        "Narrow the query: shorter timespan, lower per_page, "
        "or filter by serial/network."
    ),
) -> Any:
    """Cap the JSON-serialised size of a tool response.

    If the payload fits ``MAX_RESPONSE_BYTES``, return it unchanged.
    Otherwise return a truncation envelope:

    * For lists, include as many leading items as fit and report the
      kept-vs-total counts.
    * For dicts (or other shapes), return a single envelope with a string
      preview so the caller at least sees the schema.

    The envelope's ``_hint`` tells the model how to re-query for the part
    it actually needs.
    """
    try:
        raw = json.dumps(payload, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return payload  # let the MCP layer surface its own error

    if len(raw) <= MAX_RESPONSE_BYTES:
        return payload

    if isinstance(payload, list):
        kept: list[Any] = []
        running = 0
        for item in payload:
            chunk = len(json.dumps(item, separators=(",", ":"), default=str))
            if running + chunk > MAX_RESPONSE_BYTES:
                break
            kept.append(item)
            running += chunk
        return {
            "_truncated": True,
            "_returned": len(kept),
            "_total": len(payload),
            "_bytes_cap": MAX_RESPONSE_BYTES,
            "_hint": hint,
            "data": kept,
        }

    return {
        "_truncated": True,
        "_original_bytes": len(raw),
        "_bytes_cap": MAX_RESPONSE_BYTES,
        "_hint": hint,
        "preview": raw[:MAX_RESPONSE_BYTES],
    }


def _project(items: Any, keep: set[str], verbose: bool) -> Any:
    """Narrow a list of dicts to the ``keep`` fields unless verbose=True."""
    if verbose or not isinstance(items, list):
        return items
    return [
        {k: v for k, v in item.items() if k in keep}
        for item in items
        if isinstance(item, dict)
    ]


# Field projections for the highest-cardinality endpoints. Keep only what
# 90% of troubleshooting questions need; verbose=True returns everything.
_NETWORK_KEEP = {
    "id", "name", "organizationId", "productTypes", "tags",
    "timeZone", "url", "notes",
}
_DEVICE_KEEP = {
    "serial", "name", "mac", "model", "networkId", "tags",
    "lanIp", "wan1Ip", "wan2Ip", "firmware", "productType",
    "address", "lat", "lng",
}
_DEVICE_STATUS_KEEP = {
    "serial", "name", "status", "lastReportedAt", "productType",
    "networkId", "publicIp", "lanIp", "model", "components",
}
_INVENTORY_KEEP = {
    "serial", "name", "mac", "model", "networkId", "tags",
    "claimedAt", "orderNumber", "productType",
}
_CLIENT_KEEP = {
    "id", "mac", "ip", "ip6", "description", "lastSeen", "firstSeen",
    "usage", "status", "vlan", "ssid", "switchport",
    "manufacturer", "os", "user", "recentDeviceSerial",
}


# ---------------------------------------------------------------------------
# ORGANIZATION TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_organizations() -> Any:
    """List every Meraki organization the API key can see.

    Diagnostic only — this server is pinned to MERAKI_ORG_ID and every other
    org-scoped tool ignores the rest of this list. Useful to confirm the
    configured org ID matches what the admin's API key actually has access to.
    """
    return _get("/organizations")


@mcp.tool()
def get_organization() -> Any:
    """Return details of the configured Meraki organization (MERAKI_ORG_ID)."""
    return _get(f"/organizations/{MERAKI_ORG_ID}")


@mcp.tool()
def get_organization_networks(verbose: bool = False) -> Any:
    """List every network inside the configured organization.

    Returns a narrowed view by default (id, name, productTypes, tags, etc.).
    Pass ``verbose=True`` for the full Meraki network record.
    """
    data = _get(f"/organizations/{MERAKI_ORG_ID}/networks")
    data = _project(data, _NETWORK_KEEP, verbose)
    return _bounded(data, hint="Set verbose=True for full fields, or filter by tag client-side.")


@mcp.tool()
def get_organization_inventory_devices(verbose: bool = False) -> Any:
    """Return the device inventory for the configured organization.

    Narrowed by default; pass ``verbose=True`` for the full record.
    """
    data = _get(f"/organizations/{MERAKI_ORG_ID}/inventoryDevices")
    data = _project(data, _INVENTORY_KEEP, verbose)
    return _bounded(data, hint="Set verbose=True for full inventory fields.")


@mcp.tool()
def get_organization_uplinks_statuses() -> Any:
    """Return uplink status for all MX/MG/Z devices in the configured organization."""
    data = _get(f"/organizations/{MERAKI_ORG_ID}/uplinks/statuses")
    return _bounded(data, hint="Large orgs: ask for a specific device's uplink settings via get_device_uplink_settings.")


@mcp.tool()
def get_organization_alerts_current() -> Any:
    """Return the current alert overview for the configured org, grouped by type."""
    return _get(f"/organizations/{MERAKI_ORG_ID}/alerts/current/overview/byType")


@mcp.tool()
def get_organization_api_requests_overview() -> Any:
    """Return API usage statistics (calls made, error counts) for the configured org."""
    return _get(f"/organizations/{MERAKI_ORG_ID}/apiRequests/overview")


@mcp.tool()
def get_organization_admins() -> Any:
    """List all dashboard administrators configured on the organization."""
    return _get(f"/organizations/{MERAKI_ORG_ID}/admins")


@mcp.tool()
def get_organization_licenses_overview() -> Any:
    """Return a summary of license state for the configured organization."""
    return _get(f"/organizations/{MERAKI_ORG_ID}/licenses/overview")


@mcp.tool()
def get_organization_devices(verbose: bool = False) -> Any:
    """List every device claimed into the configured organization.

    Narrowed by default; pass ``verbose=True`` for full device records.
    """
    data = _get(f"/organizations/{MERAKI_ORG_ID}/devices")
    data = _project(data, _DEVICE_KEEP, verbose)
    return _bounded(data, hint="Set verbose=True for full fields, or call get_device(serial) for a single record.")


@mcp.tool()
def get_organization_devices_statuses(verbose: bool = False) -> Any:
    """Return online/offline/alerting status for every device in the configured org.

    Narrowed by default; pass ``verbose=True`` for full status records.
    """
    data = _get(f"/organizations/{MERAKI_ORG_ID}/devices/statuses")
    data = _project(data, _DEVICE_STATUS_KEEP, verbose)
    return _bounded(data, hint="Set verbose=True for full status records, or filter client-side by status.")


@mcp.tool()
def get_organization_devices_availabilities() -> Any:
    """Return device reachability/availability history for the configured org."""
    data = _get(f"/organizations/{MERAKI_ORG_ID}/devices/availabilities")
    return _bounded(data, hint="Large orgs can return long availability histories; query a smaller scope if truncated.")


@mcp.tool()
def get_organization_configuration_changes(
    timespan: int = 3600, per_page: int = 25
) -> Any:
    """Return the change log of configuration edits in the configured org.

    Defaults: last 1 hour, 25 rows. ``timespan`` is in seconds (max
    2,678,400 = 31 days). ``per_page`` is clamped server-side to
    ``PER_PAGE_CAP`` (default 50).
    """
    per_page = _clamp_per_page(per_page)
    data = _get(
        f"/organizations/{MERAKI_ORG_ID}/configurationChanges",
        params={"timespan": timespan, "perPage": per_page},
    )
    return _bounded(data, hint="Lower timespan or per_page; active orgs generate many change records.")


# ---------------------------------------------------------------------------
# NETWORK TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_network(network_id: str) -> Any:
    """Return details of a single network."""
    return _get(f"/networks/{network_id}")


@mcp.tool()
def get_network_devices(network_id: str, verbose: bool = False) -> Any:
    """List every device claimed into the network.

    Narrowed by default; pass ``verbose=True`` for full device records.
    """
    data = _get(f"/networks/{network_id}/devices")
    data = _project(data, _DEVICE_KEEP, verbose)
    return _bounded(data, hint="Set verbose=True for full device records.")


@mcp.tool()
def get_network_clients(
    network_id: str,
    timespan: int = 3600,
    per_page: int = 50,
    verbose: bool = False,
) -> Any:
    """List clients seen on the network within ``timespan`` seconds.

    Defaults: last 1 hour, 50 records, narrowed fields. ``timespan`` max is
    2,592,000 s (30 days). ``per_page`` is clamped server-side to
    ``PER_PAGE_CAP`` (default 50). Pass ``verbose=True`` for the full
    client record (DHCP/DNS/usage breakdowns, etc.).
    """
    per_page = _clamp_per_page(per_page)
    data = _get(
        f"/networks/{network_id}/clients",
        params={"timespan": timespan, "perPage": per_page},
    )
    data = _project(data, _CLIENT_KEEP, verbose)
    return _bounded(data, hint="Shorten timespan, set verbose=True for full client fields, or query a single client via get_network_client.")


@mcp.tool()
def get_network_events(
    network_id: str, product_type: str = "appliance", per_page: int = 25
) -> Any:
    """Return the event log for a network.

    ``product_type`` must be one of: appliance, switch, wireless, camera,
    cellularGateway, systemsManager, environmental, sensor,
    wirelessController. ``per_page`` defaults to 25 and is clamped
    server-side to ``PER_PAGE_CAP``.
    """
    per_page = _clamp_per_page(per_page)
    data = _get(
        f"/networks/{network_id}/events",
        params={"productType": product_type, "perPage": per_page},
    )
    return _bounded(data, hint="Lower per_page, switch productType, or page via startingAfter/endingBefore.")


@mcp.tool()
def get_network_traffic(network_id: str, timespan: int = 3600) -> Any:
    """Return application-level traffic analysis data for a network.

    Defaults to the last 1 hour. Requires traffic analysis to be enabled in
    Network settings on the dashboard.
    """
    data = _get(
        f"/networks/{network_id}/traffic", params={"timespan": timespan}
    )
    return _bounded(data, hint="Shorten timespan; traffic analysis returns one row per app-protocol bucket.")


@mcp.tool()
def get_network_alerts_history(network_id: str) -> Any:
    """Return historical alerts that were raised on the network."""
    data = _get(f"/networks/{network_id}/alerts/history")
    return _bounded(data, hint="If truncated, query a smaller scope or summarise client-side.")


@mcp.tool()
def get_network_alerts_settings(network_id: str) -> Any:
    """Return the configured alert thresholds and recipients for the network."""
    return _get(f"/networks/{network_id}/alerts/settings")


@mcp.tool()
def get_network_topology_link_layer(network_id: str) -> Any:
    """Return the layer-2 topology (LLDP/CDP adjacency graph) for the network."""
    data = _get(f"/networks/{network_id}/topology/linkLayer")
    return _bounded(data, hint="Large topologies can be heavy; consider walking get_device_lldp_cdp per device.")


@mcp.tool()
def get_network_clients_overview(network_id: str, timespan: int = 86400) -> Any:
    """Return aggregate client counts and usage for the network.

    This endpoint is already an aggregate, so the default timespan stays 24h.
    """
    data = _get(
        f"/networks/{network_id}/clients/overview",
        params={"timespan": timespan},
    )
    return _bounded(data)


@mcp.tool()
def get_network_client(network_id: str, client_id: str) -> Any:
    """Return details of a single client (by ID, MAC, or IP) on the network."""
    return _get(f"/networks/{network_id}/clients/{client_id}")


# ---------------------------------------------------------------------------
# DEVICE TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_device(serial: str) -> Any:
    """Return details of a single device by serial number."""
    return _get(f"/devices/{serial}")


@mcp.tool()
def get_device_uplink_settings(serial: str) -> Any:
    """Return uplink configuration/settings for the device."""
    return _get(f"/devices/{serial}/uplink/settings")


@mcp.tool()
def get_device_clients(
    serial: str, timespan: int = 3600, verbose: bool = False
) -> Any:
    """List clients seen on a specific device within ``timespan`` seconds.

    Default timespan is 1 hour. Narrowed by default; pass ``verbose=True``
    for the full client record.
    """
    data = _get(f"/devices/{serial}/clients", params={"timespan": timespan})
    data = _project(data, _CLIENT_KEEP, verbose)
    return _bounded(data, hint="Shorten timespan or set verbose=True only if you need the extra fields.")


@mcp.tool()
def get_device_lldp_cdp(serial: str) -> Any:
    """Return LLDP/CDP neighbor information for the device."""
    return _get(f"/devices/{serial}/lldpCdp")


@mcp.tool()
def get_device_management_interface(serial: str) -> Any:
    """Return the management interface configuration for the device."""
    return _get(f"/devices/{serial}/managementInterface")


@mcp.tool()
def get_device_loss_and_latency_history(
    serial: str,
    ip: str = "8.8.8.8",
    timespan: int = 3600,
    uplink: str = "wan1",
) -> Any:
    """Return historical loss and latency for an MX uplink toward ``ip``.

    Defaults: probe 8.8.8.8 over wan1 for the last 1 hour.
    """
    data = _get(
        f"/devices/{serial}/lossAndLatencyHistory",
        params={"ip": ip, "timespan": timespan, "uplink": uplink},
    )
    return _bounded(data, hint="Shorten timespan; this endpoint returns one sample per minute.")


# ---------------------------------------------------------------------------
# WIRELESS (MR) TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_network_wireless_ssids(network_id: str) -> Any:
    """List all SSIDs configured on the wireless network."""
    return _get(f"/networks/{network_id}/wireless/ssids")


@mcp.tool()
def get_network_wireless_ssid(network_id: str, number: str) -> Any:
    """Return configuration of a single SSID (numbered 0-14)."""
    return _get(f"/networks/{network_id}/wireless/ssids/{number}")


@mcp.tool()
def get_network_wireless_clients_connection_stats(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return per-client wireless connection statistics for the network."""
    data = _get(
        f"/networks/{network_id}/wireless/clients/connectionStats",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan; busy networks return thousands of client rows.")


@mcp.tool()
def get_network_wireless_channel_utilization(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return per-AP channel utilization across the wireless network."""
    data = _get(
        f"/networks/{network_id}/wireless/channelUtilization",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan; samples are per-AP, per-band, per-minute.")


@mcp.tool()
def get_network_wireless_failed_connections(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return failed wireless association/auth/DHCP/DNS attempts."""
    data = _get(
        f"/networks/{network_id}/wireless/failedConnections",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan or filter by serial client-side.")


@mcp.tool()
def get_network_wireless_signal_quality_history(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return signal quality (SNR / RSSI) history for the wireless network."""
    data = _get(
        f"/networks/{network_id}/wireless/signalQualityHistory",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan; this endpoint is per-minute, per-band.")


@mcp.tool()
def get_network_wireless_data_rate_history(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return aggregate wireless data rate history for the network."""
    data = _get(
        f"/networks/{network_id}/wireless/dataRateHistory",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan to reduce sample count.")


@mcp.tool()
def get_device_wireless_status(serial: str) -> Any:
    """Return live wireless radio status for the specified AP."""
    return _get(f"/devices/{serial}/wireless/status")


@mcp.tool()
def get_device_wireless_radio_settings(serial: str) -> Any:
    """Return radio (channel/power) settings for the specified AP."""
    return _get(f"/devices/{serial}/wireless/radio/settings")


# ---------------------------------------------------------------------------
# SWITCH (MS) TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_network_switch_ports_statuses(network_id: str) -> Any:
    """Return real-time port status for every switch in the network."""
    data = _get(f"/networks/{network_id}/switch/ports/statuses")
    return _bounded(data, hint="If truncated, call get_device_switch_ports_statuses(serial) per switch.")


@mcp.tool()
def get_device_switch_ports(serial: str) -> Any:
    """Return port configuration for the specified switch."""
    data = _get(f"/devices/{serial}/switch/ports")
    return _bounded(data, hint="48-port stacks: query a single port with get_device_switch_port(serial, port_id).")


@mcp.tool()
def get_device_switch_port(serial: str, port_id: str) -> Any:
    """Return configuration of a single port on the specified switch."""
    return _get(f"/devices/{serial}/switch/ports/{port_id}")


@mcp.tool()
def get_device_switch_ports_statuses(serial: str) -> Any:
    """Return real-time status for all ports on the specified switch."""
    data = _get(f"/devices/{serial}/switch/ports/statuses")
    return _bounded(data)


@mcp.tool()
def get_network_switch_stacks(network_id: str) -> Any:
    """List configured switch stacks in the network."""
    return _get(f"/networks/{network_id}/switch/stacks")


@mcp.tool()
def get_network_switch_vlans(network_id: str) -> Any:
    """Return VLAN configuration for the network's switches."""
    return _get(f"/networks/{network_id}/switch/vlans")


@mcp.tool()
def get_network_switch_access_policies(network_id: str) -> Any:
    """Return 802.1X / MAB access policies configured on switch ports."""
    return _get(f"/networks/{network_id}/switch/accessPolicies")


# ---------------------------------------------------------------------------
# SECURITY APPLIANCE (MX) TOOLS
# ---------------------------------------------------------------------------


@mcp.tool()
def get_network_appliance_vlans(network_id: str) -> Any:
    """List all VLANs configured on the MX security appliance."""
    return _get(f"/networks/{network_id}/appliance/vlans")


@mcp.tool()
def get_network_appliance_vlan(network_id: str, vlan_id: str) -> Any:
    """Return configuration of a single VLAN on the MX."""
    return _get(f"/networks/{network_id}/appliance/vlans/{vlan_id}")


@mcp.tool()
def get_network_appliance_uplinks_usage_history(
    network_id: str, timespan: int = 3600
) -> Any:
    """Return WAN uplink usage history (sent/received bytes) for the MX."""
    data = _get(
        f"/networks/{network_id}/appliance/uplinks/usageHistory",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan; samples are per-minute per uplink.")


@mcp.tool()
def get_network_appliance_security_events(
    network_id: str, timespan: int = 3600, per_page: int = 25
) -> Any:
    """Return IDS/IPS security events for the network.

    Defaults: last 1 hour, 25 rows. ``per_page`` is clamped server-side to
    ``PER_PAGE_CAP``.
    """
    per_page = _clamp_per_page(per_page)
    data = _get(
        f"/networks/{network_id}/appliance/security/events",
        params={"timespan": timespan, "perPage": per_page},
    )
    return _bounded(data, hint="IDS events can be dense — lower timespan or per_page.")


@mcp.tool()
def get_network_appliance_firewall_l3_rules(network_id: str) -> Any:
    """Return layer-3 outbound firewall rules configured on the MX."""
    return _get(
        f"/networks/{network_id}/appliance/firewall/l3FirewallRules"
    )


@mcp.tool()
def get_network_appliance_firewall_l7_rules(network_id: str) -> Any:
    """Return layer-7 application firewall rules configured on the MX."""
    return _get(
        f"/networks/{network_id}/appliance/firewall/l7FirewallRules"
    )


@mcp.tool()
def get_network_appliance_firewall_inbound_rules(network_id: str) -> Any:
    """Return inbound firewall rules configured on the MX."""
    return _get(
        f"/networks/{network_id}/appliance/firewall/inboundFirewallRules"
    )


@mcp.tool()
def get_network_appliance_content_filtering(network_id: str) -> Any:
    """Return content-filtering categories and URL allow/deny lists."""
    return _get(f"/networks/{network_id}/appliance/contentFiltering")


@mcp.tool()
def get_network_appliance_security_intrusion(network_id: str) -> Any:
    """Return IDS/IPS intrusion-prevention settings for the MX."""
    return _get(f"/networks/{network_id}/appliance/security/intrusion")


@mcp.tool()
def get_network_appliance_security_malware(network_id: str) -> Any:
    """Return AMP / anti-malware settings for the MX."""
    return _get(f"/networks/{network_id}/appliance/security/malware")


@mcp.tool()
def get_network_appliance_ports(network_id: str) -> Any:
    """Return per-port configuration (access / trunk / disabled) for MX LAN ports."""
    return _get(f"/networks/{network_id}/appliance/ports")


@mcp.tool()
def get_network_appliance_static_routes(network_id: str) -> Any:
    """List static routes configured on the MX appliance."""
    return _get(f"/networks/{network_id}/appliance/staticRoutes")


@mcp.tool()
def get_network_appliance_site_to_site_vpn(network_id: str) -> Any:
    """Return the AutoVPN site-to-site configuration for the MX."""
    return _get(f"/networks/{network_id}/appliance/vpn/siteToSiteVpn")


@mcp.tool()
def get_organization_appliance_vpn_statuses() -> Any:
    """Return AutoVPN reachability status for every MX in the configured organization."""
    return _get(f"/organizations/{MERAKI_ORG_ID}/appliance/vpn/statuses")


@mcp.tool()
def get_organization_appliance_vpn_stats(timespan: int = 3600) -> Any:
    """Return AutoVPN performance stats (loss/latency/jitter) for the configured org."""
    data = _get(
        f"/organizations/{MERAKI_ORG_ID}/appliance/vpn/stats",
        params={"timespan": timespan},
    )
    return _bounded(data, hint="Shorten timespan; AutoVPN stats are per peer-pair, per minute.")


# ---------------------------------------------------------------------------
# CAMERA (MV) TOOLS — read-only metadata only, no video
# ---------------------------------------------------------------------------


@mcp.tool()
def get_device_camera_video_settings(serial: str) -> Any:
    """Return video quality / retention settings for an MV camera."""
    return _get(f"/devices/{serial}/camera/video/settings")


@mcp.tool()
def get_device_camera_sense_settings(serial: str) -> Any:
    """Return MV Sense (analytics) settings for an MV camera."""
    return _get(f"/devices/{serial}/camera/sense")


# ---------------------------------------------------------------------------
# EXPLICIT WRITE REFUSAL TOOL
# ---------------------------------------------------------------------------


@mcp.tool()
def attempt_write_operation(
    method: str = "POST", path: str = "/example"
) -> str:
    """Refuses any write attempt. This server is READ-ONLY by design.

    Use this tool to verify the server's read-only stance; it always
    returns the standard refusal string and performs no network I/O.
    """
    return _refuse_write(method, path)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
