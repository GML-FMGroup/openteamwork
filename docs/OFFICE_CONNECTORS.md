# Office connectors

OpenPPX uses a breadth-first first wave of office integrations to validate three reusable execution paths: an official remote MCP service, a fixed native REST adapter, and a restricted standard-protocol adapter. Each integration is intentionally shallow and read-only. Deeper provider-specific capabilities can be added after installation, authorization, permission, Runtime assembly, and audit behavior have been exercised with real accounts.

## First wave

| App | Execution path | Current capabilities | Credential | Deliberate exclusions |
|---|---|---|---|---|
| WPS Cloud Docs | Official Streamable HTTP MCP | Search cloud documents; read content, comments, and file metadata | WPS user access token | Opening links, sharing, permission management, and document mutation |
| Feishu Docs | Official Streamable HTTP MCP | Search documents; read documents, knowledge-space listings, and comments | Feishu user access token (UAT) | Managed OAuth and token refresh, tenant token mode, writes, comment mutation, and binary downloads |
| Notion | Native official REST API | Search pages; read page metadata and one bounded page of child blocks | Integration token or personal access token | Managed OAuth, writes, comments, and provider-specific database automation |
| Email | Native read-only IMAPS | List recent headers, search, and read bounded text content by UID | Email address and provider-issued IMAP authorization code or app password | SMTP, mailbox mutation, arbitrary folders, attachments, custom hosts, and unrestricted search syntax |

The IMAPS adapter currently accepts only provider domains with fixed reviewed endpoints:

- QQ Mail and Foxmail: `qq.com`, `foxmail.com`
- NetEase Mail: `163.com`, `vip.163.com`, `126.com`, `vip.126.com`, `188.com`, `vip.188.com`, `yeah.net`

Unknown domains fail before socket I/O. Every connection uses TLS certificate validation, opens `INBOX` as read-only, fetches with `BODY.PEEK`, ignores attachment parts, and bounds returned message content.

## Common lifecycle

All four Apps use the existing governed extension path:

```text
starter catalog
  -> AppDefinition install
  -> protected credentials in SecretStore
  -> AppConnection
  -> explicit Agent enablement
  -> immutable Runtime snapshot
  -> Google ADK Tool or MCPToolset
  -> ADK authorization Plugin
  -> Tool and Network permission intersection
```

The App definition declares the complete reviewed tool set. An App connection may narrow that set but cannot add undeclared tools. Credentials remain write-only Secret values and ordinary definitions, connections, inventory, diagnostics, audit records, and Tool results never contain them.

The first-party definitions can be installed from Desktop Extensions settings or through the common Action boundary:

```bash
ppx action invoke app.starter.install --input-json '{"starterId":"app-wps-cloud-docs"}'
ppx action invoke app.starter.install --input-json '{"starterId":"app-feishu-docs"}'
ppx action invoke app.starter.install --input-json '{"starterId":"app-notion"}'
ppx action invoke app.starter.install --input-json '{"starterId":"app-email"}'
```

Installation alone does not grant an Agent access. Create a connection, store its declared credentials through the protected Secret Action or Desktop, test the connection, and explicitly enable it for the target Agent. A newly enabled App appears in a newly assembled Runtime; it is not injected into an already active Runtime.

## Permission intersection

Provider authorization and OpenPPX authorization are independent. A call succeeds only if all applicable layers allow it:

1. The App definition exposes the Tool.
2. The connection keeps the Tool enabled and is assigned to the Agent.
3. The Agent's `tool.invoke` policy allows its stable Tool identity.
4. The Agent's Network policy allows the fixed provider origin and required logical access.
5. The provider accepts the protected account credential and its tenant-side scope.

This matters most for `low`: its preset intentionally denies App/MCP Tools and Network by default. A low Agent therefore needs explicit, narrow Tool and Network allow rules. Native App Tool IDs are stable:

- Notion: `openppx.app.notion_search`, `openppx.app.notion_get_page`, `openppx.app.notion_get_block_children`
- IMAPS: `openppx.app.imap_list_messages`, `openppx.app.imap_search_messages`, `openppx.app.imap_get_message`

Remote MCP tools are namespaced by the App connection to avoid collisions. For connection ID `work-docs`, WPS IDs begin with `openppx.mcp.app_wps_cloud_docs_work_docs_`, and Feishu IDs begin with `openppx.mcp.app_feishu_docs_work_docs_`. The reviewed provider Tool name declared in the starter catalog is appended to the prefix.

The matching read-only Network rules allow `connect` and `read`, never `write` or `upload`. Relevant normalized origins are:

- WPS: `https://openapi.wps.cn:443`
- Feishu: `https://mcp.feishu.cn:443`
- Notion: `https://api.notion.com:443`
- Email: the selected fixed `imaps://<reviewed-host>:993` origin

`medium`, `high`, and `root` remain subject to the same intersection even when their presets provide broader defaults. Node hard-deny rules, private/control-plane address restrictions, and connection-level Tool narrowing still win.

## Provider-specific boundaries

WPS currently requires the provider scope `delegated:kso.mcp_yundoc.readwrite`. Because that provider scope is wider than this first release, OpenPPX independently filters MCP discovery to the four reviewed read Tools and marks the remote MCP policy as logical read access. Provider write Tools remain unavailable even when the token could call them.

Feishu uses the stable developer endpoint `https://mcp.feishu.cn/mcp` and the `X-Lark-MCP-UAT` authentication header. This first release deliberately uses only user access tokens because `search-doc` is user-token-only and the product goal is access to documents visible to the connected user. The token is stored through a Secret reference and must currently be refreshed manually when it expires. OpenPPX sends `X-Lark-MCP-Allowed-Tools` with exactly `search-doc`, `fetch-doc`, `list-docs`, and `get-comments`; the local MCP Tool filter independently enforces the same set. Omitting either layer does not broaden access.

Feishu tenant access tokens, managed OAuth consent and refresh, document mutation, comment mutation, and `fetch-file` remain future work. They require lifecycle and artifact controls that should be shared by multiple providers rather than embedded as Feishu-only exceptions.

Notion sends the provider's current `Notion-Version` header and uses fixed official API paths. Search is a logical read even though the official API represents it as HTTP `POST`; OpenPPX authorizes it as Network `connect` plus `read`.

IMAPS does not accept a model-supplied server, port, mailbox, command, or fetch expression. The email domain selects a compiled endpoint, and model input is limited to a bounded result count, bounded ASCII search text, or a numeric UID returned by the adapter.

## Acceptance status

Automated tests cover catalog validation, common starter installation, Secret redaction, adapter selection, bounded arguments and responses, reviewed IMAPS endpoint selection, read-only IMAP commands, MCP Tool filtering, logical read/write Network projection, and ADK Tool/Network authorization intersection.

The remaining acceptance step is live testing with user-owned provider accounts. It must verify connection readiness, one representative read per App, permission-denied behavior after revocation, and absence of write effects in the provider audit/history. For Feishu, the account must provide a UAT issued to a self-built App with the official scopes required by the four selected tools. Live account success is not inferred from mocked transport tests.

Tencent Docs is not included in this release. Its public official materials expose enterprise-edition APIs, but OpenPPX does not yet have a verified general-purpose personal-document API or official remote MCP contract. Browser automation is not used as a connector substitute. A future Tencent Docs adapter should enter through the same AppDefinition, Secret, Tool, Network, Runtime, and audit boundaries after an enterprise tenant and API contract are available.

Official references:

- [WPS Cloud Docs MCP tools](https://open.wps.cn/documents/app-integration-dev/mcp-server/tools/mcp_kso-yundoc)
- [WPS MCP usage guide](https://open.wps.cn/documents/app-integration-dev/mcp-server/use-guide)
- [Feishu remote MCP developer guide](https://open.feishu.cn/document/mcp_open_tools/developers-call-remote-mcp-server)
- [Notion API authentication](https://developers.notion.com/reference/authentication)
- [Notion API versioning](https://developers.notion.com/reference/versioning)
- [IMAP4rev2](https://datatracker.ietf.org/doc/html/rfc9051)
