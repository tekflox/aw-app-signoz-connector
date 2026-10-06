---
name: aw-signoz-connector
description: Send this workspace's OTLP telemetry (traces, metrics, logs) through the SigNoz Connector to a SigNoz that lives somewhere else — a central instance or another workspace's aw-app-signoz. Covers the internal endpoint to point a sender at, why the destination is NOT configured in this app, and how to tell whether telemetry is actually arriving. Use whenever instrumenting something in this workspace to emit telemetry, or when telemetry has stopped showing up at the destination.
---

# aw-signoz-connector — forward telemetry, don't store it

`aw-app-signoz-connector` is one small OpenTelemetry Collector container. It
**receives** OTLP inside this workspace and **forwards** it to a SigNoz
somewhere else. It has no ClickHouse, no UI, no window, and it stores nothing
you can query.

If you want to store and browse telemetry *inside* this workspace, you want
the other app — **`aw-app-signoz`**, the full server. The two are
alternatives, not layers; installing both means core exports through the
connector and past the local server (see "Both installed" below).

## Sending telemetry to it

Point any OTLP exporter at the container, from inside this workspace:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://aw-app-signoz-connector:4318
```

- Standard OTLP/HTTP paths: `/v1/traces`, `/v1/metrics`, `/v1/logs`.
- OTLP/gRPC is on `:4317` if you need it.
- **No credential.** This receiver is internal to the workspace's own
  container network and is not published anywhere. The credential the
  *destination* wants is held by the connector, not by you.
- Do **not** add `/api/apps/signoz-connector` — that is the workspace's
  identity-gated proxy route for the app, not the path a sibling container
  uses. Container-to-container is the address above.

`aw-workspace` core needs no configuration for this: `src/api/otel.py`
detects the connector and re-points its own exporters at this address
automatically the moment the app is installed.

## The destination is NOT configured in this app

This is the single most important thing to know, and the thing most likely to
waste your time.

The connector's `endpoint` and `api_key` config fields are **managed
values**. They are written by **Settings → Observability**, which is the one
source of truth for where this workspace's telemetry goes. On every save of
that setting, core pushes the resolved destination into this app's config,
which recreates this container with the new values.

So:

- **To change where telemetry goes:** Settings → Observability. Not this
  app's settings form.
- **Editing `endpoint`/`api_key` here by hand works until the next save of
  the Observability setting, and is then overwritten.** It is not a useful
  place to experiment.
- Mode `off` / nothing configured → core pushes empty values and stops
  exporting. The connector then has no destination and exports to a dead
  loopback address, which is intentional: telemetry queues visibly instead of
  being silently discarded.

To point at the operator's central SigNoz, set Observability to **Custom**
with the central's ingest endpoint and the shared ingest token. There is no
`central` mode and nothing injects that token automatically — by design.

## Is it actually working?

Three checks, cheapest first.

**1. Is the collector up and delivering?**

```bash
curl -s http://aw-app-signoz-connector:13133/healthz
# {"ok":true,"detail":"connector running and exporting"}
```

`ok:false` means the collector is running but its exporter has failed
repeatedly — i.e. it cannot reach the destination. That is a destination
problem (wrong endpoint, wrong credential, destination down), not a connector
problem. Note this flips on a 5-minute evaluation interval, so it lags a
change you just made.

**2. What does it say about its own exports?**

```bash
aw-workspace-cli logs signoz-connector --tail 50
```

Exporter failures are logged with the real HTTP status from the destination,
which is usually the whole answer: `401`/`403` is the credential, `404` is
the endpoint path, a dial error is the host.

**3. Did it arrive?** Query the destination for
`workspace.slug = "<this workspace's slug>"`. The connector stamps that
attribute on everything it forwards, so it is how this workspace's telemetry
is separated from every other sender at the destination.

## Gotchas worth knowing before you debug

- **A queue that survives restarts.** Undelivered telemetry is buffered on
  disk (`$AW_APP_DATA/queue`), so fixing a wrong destination can be followed
  by a burst of backdated data rather than silence. That is working as
  intended.
- **Bounded, though.** The queue and the retry window are both bounded. A
  destination that has been wrong for a long time *will* have dropped data,
  and nothing will reconstruct it.
- **`auto_start: false` is a silent outage.** Core keeps exporting to this
  container's address whether it is running or not. Stopping the connector
  loses telemetry for as long as it is down; nothing warns you.
- **The double hop is a real failure link.** With the connector installed,
  core → connector → destination. A broken connector now *silences*
  telemetry that a direct export would have delivered. This is the accepted
  cost of having one place that owns queueing and retry.
- **`workspace.slug` is not a security boundary.** It is client-set. Anything
  holding the destination's credential can claim any slug. It separates
  telemetry for querying, nothing more.

## Both installed (connector + full `aw-app-signoz`)

Core sends to the **connector**, which wins over the local server — the
connector is the forwarder and owns the queue/retry behaviour. Its
destination is whatever Observability resolved, which in `auto`/`local` mode
is the local server's own **public** URL. So telemetry hairpins out to the
edge and back instead of taking the short internal path it would have taken
with no connector installed.

It works, and nothing breaks, but it is not a combination to choose on
purpose. Pick one: connector to forward elsewhere, or the full app to keep
telemetry local.
