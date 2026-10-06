# aw-app-signoz-connector

Forwards this workspace's OpenTelemetry traces, metrics and logs to a SigNoz
that lives **somewhere else** — the operator's central instance, or another
workspace's `aw-app-signoz`.

One container (~256 MB): a stock `otel/opentelemetry-collector-contrib`,
pinned. No ClickHouse, no UI, no window, nothing to query. It receives OTLP
inside the workspace, stamps `workspace.slug` on everything, and ships it on
with a disk-backed queue so an unreachable destination buffers instead of
drops.

**Not the same app as `aw-app-signoz`.** That one is the full server —
ClickHouse, query service, dashboards — for keeping telemetry *inside* this
workspace. These are alternatives, not layers:

| | `aw-app-signoz-connector` | `aw-app-signoz` |
|---|---|---|
| Job | forward elsewhere | store + browse locally |
| Containers | 1 | 7 |
| Footprint | ~256 MB | ~6 GB |
| Queryable UI | no | yes |

Design: `docs/design/signoz-central-aw-stack-and-connector-split.md` in the
`aw-workspace` repo (§2 for this app, §3.3 for the core integration).

## Using it

Point any OTLP exporter inside this workspace at the container:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://aw-app-signoz-connector:4318
```

Standard `/v1/{traces,metrics,logs}` paths; gRPC on `:4317`. No credential —
this receiver is internal to the workspace's container network and is
published nowhere. The credential the *destination* wants is held by the
connector.

`aw-workspace` core needs no setup: `src/api/otel.py` detects the app and
re-points its own exporters here the moment it is installed.

Agent-facing reference, including how to tell whether telemetry is actually
arriving: `skills/aw-signoz-connector/SKILL.md`.

## The destination is configured in Settings → Observability, not here

The `endpoint` and `api_key` config fields are **managed values**. Core's
`src/api/observability.py` pushes the resolved destination into this app's
config on every save of the Observability setting, which recreates this
container with the new values (`src/apps/routes.py::_apply_runtime_config`).

That single setting stays the source of truth. Editing these two fields in
this app's own settings form works until the next save of the Observability
setting and is then overwritten — which is why both descriptions start with
`MANAGED VALUE`.

To ship to the operator's central SigNoz: set Observability to **Custom**,
with the central's ingest endpoint and the shared ingest token. There is no
`central` mode, and nothing injects that token automatically — deliberately
(design §3.1/§10: no workspace can reach the central unless a human put the
token there).

Unconfigured (mode `off`, or nothing saved yet) → core pushes empty values
and stops exporting. The connector then has no destination and exports to a
dead loopback address on purpose: telemetry queues *visibly* rather than
being silently discarded, and the health endpoint goes unhealthy.

### One credential, two headers

`api_key` is sent as **both** `Authorization: Bearer <value>` **and**
`X-Api-Key: <value>`. The two destinations this app exists for authenticate
differently — a central SigNoz behind aw-stack validates a bearer token
(`bearertokenauth`), an `aw-app-signoz` validates the workspace's own
`X-Api-Key` — and the manifest carries one value. Each destination ignores
the header it does not use, both headers go to the same place (so no extra
exposure), and it removes a header-name setting nobody could answer
correctly from the Observability tab.

## Why there is no doctor check

The design calls for a `contributes.doctor` route reporting exporter-queue
health. The collector is already configured to answer one — its
`health_check` extension serves `{"ok": true|false}` at `:13133/healthz`,
with `check_collector_pipeline` enabled so the value reflects whether exports
are actually *landing*, not just whether the process is up.

It is not wired up, because **it would report a permanent false failure.**
Measured against this exact image on 2026-10-06:

- `health_check` writes `Content-Type: text/plain; charset=utf-8`.
- A `response_headers: {Content-Type: application/json}` override is
  *accepted into its config* (it appears as `ResponseHeaders` in the startup
  log) and then **ignored** — the body writer sets the header itself.
- Core's evaluator (`src/apps/routes.py::_app_doctor_checks`) only parses a
  body whose content-type starts with `application/json`; anything else
  becomes `{}`, so `ok` reads false.

A doctor entry here would therefore show this app as broken forever, which is
worse than declaring none — `doctor` is the one tool this workspace has for
finding real degradation, and a permanent false red trains people to ignore
it.

The three ways to close it, none of which this app can pick unilaterally:

1. **Core accepts a JSON body on a non-JSON content-type** (parse-and-shrug).
   Two lines, benefits every app, and the only option that keeps this app on
   one stock container. Needs the core-before-app rollout order.
2. **A second container** serving the JSON — contradicts the one-container
   constraint.
3. **A custom image** wrapping the collector — contradicts the stock-pinned-
   image constraint, and adds a GHCR build pipeline to this repo.

Routed to the architect rather than chosen here. Until then the endpoint is
usable by hand, and `tests/test_collector_config.py` pins both the `{"ok":
...}` shape and the *absence* of the doctor entry, so neither drifts before
the gap is closed:

```bash
curl -s http://aw-app-signoz-connector:13133/healthz
```

## Things that will bite

- **`auto_start: false` is a silent outage.** Core keeps exporting to this
  container's address whether it is up or not. Nothing warns you.
- **The double hop is a real failure link.** With this app installed, core →
  connector → destination. A broken connector now *silences* telemetry a
  direct export would have delivered. That is the accepted cost of having one
  place own queueing and retry.
- **The queue is bounded, and so is retry.** A transient outage is covered; a
  destination that has been wrong for a long time *will* have dropped data,
  and nothing reconstructs it.
- **`workspace.slug` is not a security boundary.** It is client-set, so
  anything holding the destination's credential can claim any slug. It
  separates telemetry for querying, nothing more (design §5).
- **Both apps installed** → core sends to the connector, which wins, and its
  destination in `auto`/`local` mode is the local server's *public* URL. So
  telemetry hairpins out to the edge and back instead of taking the internal
  path. It works; it is not a combination to choose on purpose.

## Development

```bash
python3 -m pytest tests/ -q                 # static invariants
python3 tests/validate_manifest.py aw-app.json
```

`tests/test_collector_config.py` has no collector in it on purpose — it pins
the invariants that otherwise fail *silently*: every `${config.x}` in the
manifest having a schema default (`expand_env` drops unresolved placeholders,
design §8.7), every `${env:...}` in the collector config having a `:-`
fallback (otelcol treats an unset reference as fatal and would crash-loop),
and `runtime.port` matching the OTLP/HTTP receiver port (core dials
`base_url()` == `http://<container>:<runtime.port>`).
