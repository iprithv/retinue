"""Curated connector catalog (§28.6): one-click integrations.

The integration bus is what already exists — MCP servers and OpenAPI actions.
A connector is a *pinned recipe*: for MCP entries, the exact command/URL plus
which secrets it needs; for API entries, a bundled minimal OpenAPI subset with
the right auth shape and host allowlist. Installing one creates the
corresponding McpServer or OpenApiAction row; nothing new is invented at the
transport layer, so every §16 posture (encrypted secrets, SSRF guard,
untrusted-results fencing) applies automatically.

MCP recipes reference publicly published servers; stdio ones need their
runtime (npx/uvx/docker) present on the host — install surfaces that
requirement instead of hiding it.
"""

from dataclasses import dataclass, field
from typing import Any

CATALOG_VERSION = 1


@dataclass(slots=True, frozen=True)
class SecretField:
    name: str  # env var (stdio), header (http), or auth key (openapi)
    label: str
    required: bool = True


@dataclass(slots=True, frozen=True)
class ParamField:
    name: str  # substituted into command args / urls / spec servers
    label: str
    required: bool = True
    default: str = ""


@dataclass(slots=True, frozen=True)
class Connector:
    key: str
    name: str
    category: str  # chat|dev|tickets|observability|incidents|cloud|docs|crm|search
    kind: str  # mcp-stdio | mcp-http | openapi
    description: str
    secrets: tuple[SecretField, ...] = ()
    params: tuple[ParamField, ...] = ()
    # mcp-stdio
    command: str = ""
    args: tuple[str, ...] = ()
    runtime: str = ""  # npx|uvx|docker|binary — surfaced as a host requirement
    # mcp-http
    url: str = ""
    # openapi
    spec: dict[str, Any] = field(default_factory=dict)
    auth_type: str = ""  # api_key_header|bearer|basic
    auth_header: str = ""
    host_allowlist: tuple[str, ...] = ()
    docs: str = ""


def _openapi(title: str, base_url: str, paths: dict[str, Any]) -> dict[str, Any]:
    return {
        "openapi": "3.0.0",
        "info": {"title": title, "version": "1"},
        "servers": [{"url": base_url}],
        "paths": paths,
    }


def _get_op(operation_id: str, summary: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "get": {
            "operationId": operation_id,
            "summary": summary,
            "parameters": [
                {
                    "name": p["name"],
                    "in": p.get("in", "query"),
                    "required": p.get("required", False),
                    "schema": p.get("schema", {"type": "string"}),
                    "description": p.get("description", ""),
                }
                for p in params
            ],
        }
    }


CONNECTORS: dict[str, Connector] = {
    c.key: c
    for c in [
        # -- chat & collaboration ----------------------------------------------------
        Connector(
            key="slack",
            name="Slack",
            category="chat",
            kind="mcp-stdio",
            description="Read channels, post messages, search history as agent tools.",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-slack"),
            runtime="npx",
            secrets=(
                SecretField("SLACK_BOT_TOKEN", "Bot token (xoxb-…)"),
                SecretField("SLACK_TEAM_ID", "Team ID (T…)"),
            ),
            docs=(
                "https://api.slack.com/apps — bot scopes: channels:history, "
                "channels:read, chat:write, users:read"
            ),
        ),
        Connector(
            key="slack-webhook",
            name="Slack (incoming webhook)",
            category="chat",
            kind="openapi",
            description="Post notifications to one channel — no app install needed.",
            params=(ParamField("webhook_path", "Webhook path (/services/T…/B…/…)"),),
            spec=_openapi(
                "Slack Webhook",
                "https://hooks.slack.com",
                {
                    "{webhook_path}": {
                        "post": {
                            "operationId": "postMessage",
                            "summary": "Post a message to the configured channel",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"text": {"type": "string"}},
                                            "required": ["text"],
                                        }
                                    }
                                },
                            },
                        }
                    }
                },
            ),
            host_allowlist=("hooks.slack.com",),
        ),
        Connector(
            key="discord-webhook",
            name="Discord (webhook)",
            category="chat",
            kind="openapi",
            description="Post notifications to a Discord channel via webhook.",
            params=(ParamField("webhook_path", "Webhook path (/api/webhooks/…)"),),
            spec=_openapi(
                "Discord Webhook",
                "https://discord.com",
                {
                    "{webhook_path}": {
                        "post": {
                            "operationId": "postMessage",
                            "summary": "Post a message",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"content": {"type": "string"}},
                                            "required": ["content"],
                                        }
                                    }
                                },
                            },
                        }
                    }
                },
            ),
            host_allowlist=("discord.com",),
        ),
        # -- dev tools -------------------------------------------------------------------
        Connector(
            key="github",
            name="GitHub",
            category="dev",
            kind="mcp-stdio",
            description="Repos, issues, PRs, code search — the full GitHub MCP server.",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-github"),
            runtime="npx",
            secrets=(SecretField("GITHUB_PERSONAL_ACCESS_TOKEN", "Personal access token"),),
        ),
        Connector(
            key="gitlab",
            name="GitLab",
            category="dev",
            kind="mcp-stdio",
            description="Projects, issues, and MRs on gitlab.com or self-hosted.",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-gitlab"),
            runtime="npx",
            secrets=(
                SecretField("GITLAB_PERSONAL_ACCESS_TOKEN", "Personal access token"),
                SecretField("GITLAB_API_URL", "API URL", required=False),
            ),
        ),
        Connector(
            key="kubernetes",
            name="Kubernetes",
            category="cloud",
            kind="mcp-stdio",
            description="Inspect pods, deployments, logs, and events via kubectl context.",
            command="npx",
            args=("-y", "mcp-server-kubernetes"),
            runtime="npx",
            docs="uses the host's kubeconfig; grant a read-only context",
        ),
        Connector(
            key="aws",
            name="AWS",
            category="cloud",
            kind="mcp-stdio",
            description="Query AWS APIs (EC2, CloudWatch, S3 metadata, …).",
            command="uvx",
            args=("awslabs.aws-api-mcp-server@latest",),
            runtime="uvx",
            secrets=(
                SecretField("AWS_ACCESS_KEY_ID", "Access key ID"),
                SecretField("AWS_SECRET_ACCESS_KEY", "Secret access key"),
                SecretField("AWS_REGION", "Region", required=False),
            ),
        ),
        # -- tickets & project management ---------------------------------------------------
        Connector(
            key="atlassian",
            name="Jira & Confluence",
            category="tickets",
            kind="mcp-stdio",
            description="Search/read/create issues and pages (Cloud or Server).",
            command="uvx",
            args=("mcp-atlassian",),
            runtime="uvx",
            secrets=(
                SecretField("JIRA_URL", "Jira URL (https://you.atlassian.net)"),
                SecretField("JIRA_USERNAME", "Email"),
                SecretField("JIRA_API_TOKEN", "API token"),
                SecretField("CONFLUENCE_URL", "Confluence URL", required=False),
                SecretField("CONFLUENCE_USERNAME", "Confluence email", required=False),
                SecretField("CONFLUENCE_API_TOKEN", "Confluence token", required=False),
            ),
        ),
        Connector(
            key="linear",
            name="Linear",
            category="tickets",
            kind="mcp-http",
            description="Issues, projects, and cycles via Linear's hosted MCP server.",
            url="https://mcp.linear.app/mcp",
            secrets=(SecretField("Authorization", "Bearer <Linear API key>"),),
        ),
        Connector(
            key="notion",
            name="Notion",
            category="docs",
            kind="mcp-stdio",
            description="Search and read pages/databases in your workspace.",
            command="npx",
            args=("-y", "@notionhq/notion-mcp-server"),
            runtime="npx",
            secrets=(SecretField("NOTION_TOKEN", "Internal integration token (ntn_…)"),),
        ),
        Connector(
            key="zendesk",
            name="Zendesk",
            category="crm",
            kind="openapi",
            description="Search tickets and users (read-only subset).",
            params=(ParamField("subdomain", "Zendesk subdomain"),),
            auth_type="basic",
            secrets=(
                SecretField("user", "User (email/token for API-token auth)"),
                SecretField("password", "Password or API token"),
            ),
            spec=_openapi(
                "Zendesk",
                "https://{subdomain}.zendesk.com",
                {
                    "/api/v2/search.json": _get_op(
                        "search",
                        "Search tickets/users/orgs (Zendesk query syntax)",
                        [{"name": "query", "required": True}],
                    ),
                    "/api/v2/tickets/{id}.json": _get_op(
                        "getTicket",
                        "Fetch one ticket",
                        [{"name": "id", "in": "path", "required": True}],
                    ),
                },
            ),
            docs="basic auth: email/token as user 'you@co.com/token', API token as password",
        ),
        Connector(
            key="servicenow",
            name="ServiceNow",
            category="tickets",
            kind="openapi",
            description="Query any table via the Table API (read-only subset).",
            params=(ParamField("instance", "Instance name (….service-now.com)"),),
            auth_type="basic",
            secrets=(
                SecretField("user", "Username"),
                SecretField("password", "Password"),
            ),
            spec=_openapi(
                "ServiceNow",
                "https://{instance}.service-now.com",
                {
                    "/api/now/table/{table}": _get_op(
                        "queryTable",
                        "Query records from a table",
                        [
                            {"name": "table", "in": "path", "required": True},
                            {"name": "sysparm_query", "description": "encoded query"},
                            {
                                "name": "sysparm_limit",
                                "schema": {"type": "integer"},
                            },
                        ],
                    )
                },
            ),
        ),
        # -- observability -----------------------------------------------------------------
        Connector(
            key="grafana",
            name="Grafana",
            category="observability",
            kind="mcp-stdio",
            description="Dashboards, datasource queries, alerts, and incidents.",
            command="mcp-grafana",
            args=(),
            runtime="binary",
            secrets=(
                SecretField("GRAFANA_URL", "Grafana URL"),
                SecretField("GRAFANA_API_KEY", "Service account token"),
            ),
            docs="install the mcp-grafana binary from grafana/mcp-grafana releases",
        ),
        Connector(
            key="prometheus",
            name="Prometheus",
            category="observability",
            kind="openapi",
            description="PromQL instant/range queries and series metadata.",
            params=(ParamField("base_url", "Prometheus base URL (http://host:9090)"),),
            spec=_openapi(
                "Prometheus",
                "{base_url}",
                {
                    "/api/v1/query": _get_op(
                        "instantQuery",
                        "Evaluate a PromQL expression now",
                        [{"name": "query", "required": True, "description": "PromQL"}],
                    ),
                    "/api/v1/query_range": _get_op(
                        "rangeQuery",
                        "Evaluate PromQL over a time range",
                        [
                            {"name": "query", "required": True},
                            {"name": "start", "required": True, "description": "rfc3339|unix"},
                            {"name": "end", "required": True},
                            {"name": "step", "required": True, "description": "e.g. 30s"},
                        ],
                    ),
                    "/api/v1/series": _get_op(
                        "series",
                        "Find series by label matchers",
                        [{"name": "match[]", "required": True}],
                    ),
                    "/api/v1/label/{label}/values": _get_op(
                        "labelValues",
                        "List values for a label",
                        [{"name": "label", "in": "path", "required": True}],
                    ),
                },
            ),
        ),
        Connector(
            key="datadog",
            name="Datadog",
            category="observability",
            kind="mcp-stdio",
            description="Metrics, monitors, logs, and incidents.",
            command="npx",
            args=("-y", "@winor30/mcp-server-datadog"),
            runtime="npx",
            secrets=(
                SecretField("DATADOG_API_KEY", "API key"),
                SecretField("DATADOG_APP_KEY", "Application key"),
                SecretField("DATADOG_SITE", "Site (datadoghq.eu, …)", required=False),
            ),
        ),
        Connector(
            key="sentry",
            name="Sentry",
            category="observability",
            kind="mcp-http",
            description="Issues, events, and traces via Sentry's hosted MCP server.",
            url="https://mcp.sentry.dev/mcp",
            secrets=(SecretField("Authorization", "Bearer <user auth token>"),),
        ),
        Connector(
            key="newrelic",
            name="New Relic",
            category="observability",
            kind="openapi",
            description="Run NRQL/NerdGraph queries.",
            auth_type="api_key_header",
            auth_header="API-Key",
            secrets=(SecretField("key", "User API key (NRAK-…)"),),
            spec=_openapi(
                "New Relic",
                "https://api.newrelic.com",
                {
                    "/graphql": {
                        "post": {
                            "operationId": "nerdgraph",
                            "summary": "Run a NerdGraph (GraphQL) query, incl. NRQL",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"query": {"type": "string"}},
                                            "required": ["query"],
                                        }
                                    }
                                },
                            },
                        }
                    }
                },
            ),
            host_allowlist=("api.newrelic.com",),
        ),
        Connector(
            key="splunk",
            name="Splunk",
            category="observability",
            kind="openapi",
            description="Run one-shot searches over the REST API.",
            params=(ParamField("base_url", "Splunk REST URL (https://host:8089)"),),
            auth_type="bearer",
            secrets=(SecretField("token", "Bearer token"),),
            spec=_openapi(
                "Splunk",
                "{base_url}",
                {
                    "/services/search/jobs/oneshot": {
                        "post": {
                            "operationId": "oneshotSearch",
                            "summary": "Run a search and return results inline",
                            "parameters": [
                                {
                                    "name": "search",
                                    "in": "query",
                                    "required": True,
                                    "schema": {"type": "string"},
                                    "description": 'e.g. "search index=main error | head 20"',
                                },
                                {
                                    "name": "output_mode",
                                    "in": "query",
                                    "required": False,
                                    "schema": {"type": "string", "default": "json"},
                                },
                            ],
                        }
                    }
                },
            ),
        ),
        # -- incidents ---------------------------------------------------------------------------
        Connector(
            key="pagerduty",
            name="PagerDuty",
            category="incidents",
            kind="openapi",
            description="List and inspect incidents, services, and on-calls.",
            auth_type="api_key_header",
            auth_header="Authorization",
            secrets=(SecretField("key", "Token token=<API key>"),),
            spec=_openapi(
                "PagerDuty",
                "https://api.pagerduty.com",
                {
                    "/incidents": _get_op(
                        "listIncidents",
                        "List incidents",
                        [
                            {
                                "name": "statuses[]",
                                "description": "triggered|acknowledged|resolved",
                            },
                            {"name": "limit", "schema": {"type": "integer"}},
                        ],
                    ),
                    "/incidents/{id}": _get_op(
                        "getIncident",
                        "Fetch one incident",
                        [{"name": "id", "in": "path", "required": True}],
                    ),
                    "/oncalls": _get_op("listOncalls", "Who is on call", []),
                    "/services": _get_op("listServices", "List services", []),
                },
            ),
            host_allowlist=("api.pagerduty.com",),
        ),
        Connector(
            key="opsgenie",
            name="Opsgenie",
            category="incidents",
            kind="openapi",
            description="List and inspect alerts.",
            auth_type="api_key_header",
            auth_header="Authorization",
            secrets=(SecretField("key", "GenieKey <API key>"),),
            spec=_openapi(
                "Opsgenie",
                "https://api.opsgenie.com",
                {
                    "/v2/alerts": _get_op(
                        "listAlerts",
                        "List alerts",
                        [
                            {"name": "query", "description": "e.g. status:open"},
                            {"name": "limit", "schema": {"type": "integer"}},
                        ],
                    ),
                    "/v2/alerts/{id}": _get_op(
                        "getAlert",
                        "Fetch one alert",
                        [{"name": "id", "in": "path", "required": True}],
                    ),
                },
            ),
            host_allowlist=("api.opsgenie.com",),
        ),
        # -- docs & drive ------------------------------------------------------------
        Connector(
            key="gdrive",
            name="Google Drive",
            category="docs",
            kind="mcp-stdio",
            description="Search and read files in Google Drive.",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-gdrive"),
            runtime="npx",
            docs="requires OAuth setup per the server's README (credentials file on the host)",
        ),
        Connector(
            key="intercom",
            name="Intercom",
            category="crm",
            kind="openapi",
            description="Search conversations and contacts (read-only subset).",
            auth_type="bearer",
            secrets=(SecretField("token", "Access token"),),
            spec=_openapi(
                "Intercom",
                "https://api.intercom.io",
                {
                    "/conversations": _get_op(
                        "listConversations",
                        "List conversations",
                        [{"name": "per_page", "schema": {"type": "integer"}}],
                    ),
                    "/contacts": _get_op("listContacts", "List contacts", []),
                },
            ),
            host_allowlist=("api.intercom.io",),
        ),
    ]
}


def connector_catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": c.key,
            "name": c.name,
            "category": c.category,
            "kind": c.kind,
            "description": c.description,
            "secrets": [
                {"name": s.name, "label": s.label, "required": s.required} for s in c.secrets
            ],
            "params": [
                {"name": p.name, "label": p.label, "required": p.required, "default": p.default}
                for p in c.params
            ],
            "runtime": c.runtime,
            "docs": c.docs,
        }
        for c in CONNECTORS.values()
    ]


def substitute_params(template: str, params: dict[str, str]) -> str:
    out = template
    for key, value in params.items():
        out = out.replace("{" + key + "}", value)
    return out
