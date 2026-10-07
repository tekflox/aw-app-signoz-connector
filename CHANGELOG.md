# Changelog

## 0.3.0

Query read-path + a window for the destination's own web UI — this app no
longer only *forwards* telemetry, it can also *read it back*. Three new
MANAGED fields (`query_mcp_url`, `query_api_key`, `web_ui_url`), pushed by
the same `src/api/observability.py::_push_connector_config` seam that
already manages `endpoint`/`api_key`.

**Query MCP tools, no sidecar.** New `mcp.template.json` (`type: http`,
precedent: aw-app-browser's own sidecar-less stdio upstream) renders a
`signoz-query` server pointed at `${config.query_mcp_url}` with
`SIGNOZ-API-KEY: ${config.query_api_key}`. `aw-app-signoz`'s own `mcp`
sidecar is untouched — it stays local-bound to that app's own instance.
Empty config disables the upstream (same `src/apps/mcp_template.py` seam
aw-app-signoz already proves) instead of connecting and 401ing on every
call.

**New window.** A declarative window (`windows/signoz_ui.json`) embeds the
destination's web UI via the existing `iframe` widget, `src:
"${config.web_ui_url}"` — needs aw-workspace core's `_resolve_window` to
expand `${config.x}` in a declarative window spec (new in core as of this
release) and the `iframe` widget's empty-src guard (also new) so an unset
`web_ui_url` hides the window instead of rendering a blank frame. No
start/stop/restart control of any kind — this app never owned the
lifecycle of whatever it points at, and that stays true here.

## 0.1.0

First release. One pinned `otel/opentelemetry-collector-contrib` container
that receives OTLP inside the workspace (`:4318` HTTP, `:4317` gRPC, internal
only) and forwards traces, metrics and logs to a destination configured in
**Settings → Observability** — not in this app. Core pushes the resolved
destination into `endpoint`/`api_key` on every save of that setting, which
recreates the container; both fields are marked `MANAGED VALUE` for that
reason.

Pipeline is `memory_limiter` → `resource` (upserting `workspace.slug`) →
`batch` → `otlphttp`, with a retrying `sending_queue` so a brief
destination outage buffers rather than dropping. That queue is **in-memory**,
not the `file_storage`-backed one on `$AW_APP_DATA` the design asked for:
built that way first, it crash-looped on `permission denied` because the
stock image runs as `USER 10001` and cannot write the bind-mount directory
core creates as the workspace uid — and a Tier-2 manifest can override
neither side. So a transient outage is covered, a container recreate is not.
The credential goes out as both
`Authorization: Bearer` and `X-Api-Key`, since a central SigNoz and an
`aw-app-signoz` authenticate differently and there is one value to carry.

No window, no public ingest route, and — see README.md "Why there is no
doctor check" — no `contributes.doctor` entry yet: the collector's health
endpoint already answers the right `{"ok": bool}` shape, but serves it as
`text/plain`, which core's doctor evaluator cannot read. Declaring it would
report a permanent false failure. The decision on how to close that is with
the architect.

Design: `docs/design/signoz-central-aw-stack-and-connector-split.md` §2/§3.3
in the `aw-workspace` repo.
