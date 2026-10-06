# Changelog

## 0.1.0

First release. One pinned `otel/opentelemetry-collector-contrib` container
that receives OTLP inside the workspace (`:4318` HTTP, `:4317` gRPC, internal
only) and forwards traces, metrics and logs to a destination configured in
**Settings → Observability** — not in this app. Core pushes the resolved
destination into `endpoint`/`api_key` on every save of that setting, which
recreates the container; both fields are marked `MANAGED VALUE` for that
reason.

Pipeline is `memory_limiter` → `resource` (upserting `workspace.slug`) →
`batch` → `otlphttp`, with a `file_storage`-backed `sending_queue` on
`$AW_APP_DATA` so an unreachable destination buffers across container
recreates rather than dropping. The credential goes out as both
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
