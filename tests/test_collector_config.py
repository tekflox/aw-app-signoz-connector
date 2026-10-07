"""The manifest and the collector config have to agree with each other, and
with two aw-workspace framework behaviours that fail SILENTLY when they don't.

Nothing here starts a collector — these are the static invariants that turn a
silent misconfiguration into a red test. Each one has a real failure behind
it, named in the test.
"""
from __future__ import annotations

import json
import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "container", "otelcol", "config.yaml")


@pytest.fixture(scope="module")
def manifest() -> dict:
    with open(os.path.join(ROOT, "aw-app.json")) as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def config_text() -> str:
    with open(CONFIG_PATH) as fh:
        return fh.read()


@pytest.fixture(scope="module")
def config(config_text) -> dict:
    return yaml.safe_load(config_text)


# --- the two framework traps -------------------------------------------------


def test_every_config_placeholder_in_runtime_env_has_a_schema_default(manifest):
    """src/apps/containers.py::expand_env DROPS an unresolved ``${config.x}``
    rather than passing an empty string, and ``config_with_defaults`` is what
    supplies a value for a config nobody has saved yet. A referenced key with
    no schema default therefore yields a collector missing that variable
    entirely on a fresh install — the CRISPAL_SITE_URL incident
    (src/apps/runtime.py:1468), and design §8.7 of the SigNoz split."""
    declared = (manifest["runtime"].get("env") or {})
    properties = manifest["config_schema"]["properties"]

    referenced = set()
    for raw in declared.values():
        referenced.update(re.findall(r"\$\{config\.([A-Za-z0-9_]+)", str(raw)))

    assert referenced, "expected runtime.env to reference at least one config key"
    for key in sorted(referenced):
        assert key in properties, f"runtime.env references ${{config.{key}}} with no schema entry"
        assert "default" in properties[key], (
            f"${{config.{key}}} has no schema default — a never-saved config would "
            f"drop it from the container's environment entirely")


def test_every_env_placeholder_in_the_collector_config_has_a_fallback(config_text):
    """An EMPTY config value still resolves to None in expand_env, so the
    variable is still dropped — and otelcol treats a referenced-but-UNSET
    variable as a fatal config error, crash-looping the container instead of
    degrading. Every ``${env:VAR}`` must therefore carry a ``:-`` default."""
    referenced = re.findall(r"\$\{env:([A-Za-z0-9_]+)(:-[^}]*)?\}", config_text)

    assert referenced, "expected the collector config to read some ${env:...} values"
    missing = [name for name, fallback in referenced if not fallback]
    assert not missing, (
        f"these ${{env:...}} references have no ':-' fallback and would make the "
        f"collector refuse to start when unset: {missing}")


def test_the_env_vars_the_config_reads_are_the_ones_the_manifest_sets(manifest, config_text):
    """A rename on one side only is invisible until telemetry stops: the
    config falls back to its default and the manifest's variable goes
    nowhere, with no error on either side."""
    set_by_manifest = set(manifest["runtime"].get("env") or {})
    read_by_config = {name for name, _ in
                      re.findall(r"\$\{env:([A-Za-z0-9_]+)(:-[^}]*)?\}", config_text)}

    assert read_by_config <= set_by_manifest, (
        f"the collector config reads variables the manifest never sets: "
        f"{sorted(read_by_config - set_by_manifest)}")


# --- the core↔connector contract --------------------------------------------


def test_manifest_port_is_the_otlp_http_port_core_will_dial(manifest, config):
    """aw-workspace core reaches this app at
    ``runtime.containers.base_url(app_id)`` == ``http://<container>:<runtime.port>``
    and appends ``/v1/logs`` etc. (src/api/otel.py::ensure_export_state). So
    runtime.port MUST be the OTLP/HTTP receiver's port — any other value and
    core exports into a port nothing is listening on, silently."""
    http_endpoint = config["receivers"]["otlp"]["protocols"]["http"]["endpoint"]

    assert http_endpoint.endswith(f":{manifest['runtime']['port']}"), (
        f"runtime.port={manifest['runtime']['port']} does not match the OTLP/HTTP "
        f"receiver endpoint {http_endpoint!r}")


def test_the_app_publishes_no_ingest_route_or_nav(manifest):
    """Design §2: the connector's own OTLP consumers are all inside the
    workspace, so it publishes no ingest route or nav entry. It DOES
    contribute one window as of 0.3.0 (design §11) — an iframe onto the
    destination's own web UI, not a UI this app renders itself — see
    test_signoz_query_mcp.py for that window's own coverage."""
    contributes = manifest.get("contributes", {})

    assert not contributes.get("routes")
    assert not contributes.get("nav")


def test_managed_config_fields_say_so_in_their_description(manifest):
    """endpoint/api_key are written by Settings > Observability on every save
    (src/api/observability.py::_push_connector_config). A user who edits them
    here loses the edit on the next save, so the form has to say that —
    ``provision_status`` in aw-app-signoz is the precedent for a
    framework-written config field announcing itself."""
    properties = manifest["config_schema"]["properties"]

    for key in ("endpoint", "api_key"):
        assert "MANAGED VALUE" in properties[key]["description"], (
            f"config field {key!r} is framework-written but its description "
            f"does not warn the user")


def test_the_destination_credential_is_marked_secret(manifest):
    """x-secret is a UI hint (it does not change storage), but the api_key
    field holds the shared fleet ingest token — it must not render as plain
    text in the settings form."""
    assert manifest["config_schema"]["properties"]["api_key"].get("x-secret") is True


# --- pipeline shape ----------------------------------------------------------


def test_all_three_signals_are_forwarded(config):
    """Logs were the original ask, but core exports traces too and apps emit
    metrics — a pipeline missing here drops that signal with no error."""
    pipelines = config["service"]["pipelines"]

    for signal in ("traces", "metrics", "logs"):
        assert signal in pipelines, f"no {signal} pipeline"
        assert pipelines[signal]["exporters"] == ["otlphttp/upstream"]


def test_every_pipeline_starts_with_the_memory_limiter(config):
    """The container is capped at 256 MB and an unreachable destination is
    exactly when buffered data grows. memory_limiter has to be FIRST to
    refuse new data under pressure; behind batch it would admit a batch it
    cannot hold."""
    for signal, pipeline in config["service"]["pipelines"].items():
        assert pipeline["processors"][0] == "memory_limiter", (
            f"{signal} pipeline does not start with memory_limiter")


def test_every_pipeline_stamps_the_workspace_slug(config):
    """At the destination this attribute is the only thing separating this
    workspace's telemetry from another sender's. A pipeline that skips it
    produces telemetry nobody can attribute."""
    for signal, pipeline in config["service"]["pipelines"].items():
        assert "resource/workspace" in pipeline["processors"], (
            f"{signal} pipeline does not stamp workspace.slug")


def test_the_sending_queue_is_enabled_but_in_memory_with_no_app_data_volume(manifest, config):
    """REGRESSION GUARD for a measured failure, not a style preference.

    The design (§2) asks for a ``file_storage``-backed queue on
    ``$AW_APP_DATA``. It was built that way FIRST, on a real install, and
    crash-looped::

        cannot start pipelines: failed to start "otlphttp/upstream" exporter:
        open /var/lib/otelcol/storage/exporter_otlphttp_upstream_logs:
        permission denied

    This image is ``USER 10001:10001``; core creates the ``$AW_APP_DATA``
    bind-mount directory as the workspace's own uid (1001) with mode 0755;
    and a Tier-2 manifest can change neither side — there is no ``user``
    field and ``_parse_run_flags`` accepts only ``--shm-size``. Core's
    unconditional CAP_CHOWN grant for Tier-2 containers only helps an image
    whose entrypoint runs as root and chowns the mount itself, which a stock
    image with a fixed USER cannot do.

    So this asserts the CURRENT, WORKING shape: queue on, no file_storage, no
    ``$AW_APP_DATA`` volume, no ``fs:workspace-data``. Re-adding any of them
    without first solving the uid problem reintroduces a crash loop that an
    install still reports as a running app. Flip this test deliberately, in
    the change that fixes it.
    """
    queue = config["exporters"]["otlphttp/upstream"]["sending_queue"]

    assert queue["enabled"] is True
    assert "storage" not in queue
    assert "file_storage/queue" not in (config.get("extensions") or {})
    assert "file_storage/queue" not in config["service"]["extensions"]

    sources = [v["source"] for v in manifest["runtime"]["volumes"]]
    assert not [s for s in sources if s.startswith("$AW_APP_DATA")], (
        "an $AW_APP_DATA volume is declared again — read this test's docstring")
    assert "fs:workspace-data" not in manifest["permissions"], (
        "fs:workspace-data is back but nothing writes to $AW_APP_DATA")


def test_the_collector_does_not_export_its_own_internal_metrics(config):
    """Self-telemetry would ride the same exporter to the same destination, so
    a FAILING exporter would emit metrics about its own failure through the
    thing that is failing. Same amplification class as
    src/api/otel.py::_AMPLIFICATION_PREFIXES."""
    assert config["service"]["telemetry"]["metrics"]["level"] == "none"


def test_retry_is_bounded(config):
    """Unbounded retry against a permanently-wrong destination pins the
    exporter on ancient batches and starves current telemetry."""
    retry = config["exporters"]["otlphttp/upstream"]["retry_on_failure"]

    assert retry["enabled"] is True
    assert retry.get("max_elapsed_time"), "retry_on_failure has no max_elapsed_time"


def test_the_health_endpoint_answers_the_doctor_shape(config):
    """The body is already ``{"ok": bool}`` so this can feed a
    contributes.doctor check the day core accepts it. It cannot today — the
    extension writes text/plain and ignores a Content-Type override, measured
    2026-10-06 — which is why no doctor entry is declared; see README.md.
    Pinned so the shape is not "cleaned up" before that gap is closed."""
    body = config["extensions"]["health_check"]["response_body"]

    assert json.loads(body["healthy"])["ok"] is True
    assert json.loads(body["unhealthy"])["ok"] is False
    assert config["extensions"]["health_check"]["check_collector_pipeline"]["enabled"] is True


def test_no_doctor_check_is_declared_while_that_gap_is_open(manifest):
    """Paired with the test above. A doctor entry pointed at the health
    endpoint would report ok:false forever (core only parses an
    application/json body — src/apps/routes.py::_app_doctor_checks), which is
    worse than declaring none: doctor would show a permanent false failure
    for a healthy app. Delete this test in the same change that adds the
    entry, once core accepts the response or the app grows something that can
    serve JSON."""
    assert not manifest.get("contributes", {}).get("doctor"), (
        "a doctor check is declared — if core's content-type handling was "
        "fixed, remove this test; otherwise it will read ok:false forever")


def test_the_image_tag_is_pinned(manifest):
    """A moving tag turns an unrelated container recreate into an
    unattributable change of collector version."""
    image = manifest["runtime"]["image"]

    assert image.startswith("otel/opentelemetry-collector-contrib:")
    assert not image.endswith(":latest")
