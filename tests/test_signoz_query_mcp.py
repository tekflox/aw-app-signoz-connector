"""Coverage for the no-sidecar query MCP read-path (mcp.template.json) and
the window that embeds the destination's web UI.

`tests/validate_manifest.py` checks the manifest shape generically; this
locks the two decisions this feature actually depends on:

1. `query_mcp_url` / `query_api_key` resolve a `type: http` server with NO
   sidecar — the precedent is aw-app-browser's own stdio upstream pointed
   at a different container's CDP endpoint, not aw-app-signoz's `mcp`
   sidecar (that one stays local-bound, untouched by this app).
2. An unresolved placeholder (empty config) disables the upstream instead
   of connecting and 401ing on every call — same seam
   `src/apps/mcp_template.py` already proves for aw-app-signoz
   (`test_empty_key_disables_the_upstream_instead_of_401ing_every_call`).

See docs/design/signoz-central-aw-stack-and-connector-split.md §11 in the
aw-workspace repo for the full design.
"""
from __future__ import annotations

import json
import os

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(APP_ROOT, "aw-app.json")
MCP_TEMPLATE_PATH = os.path.join(APP_ROOT, "mcp.template.json")
WINDOW_SPEC_PATH = os.path.join(APP_ROOT, "windows", "signoz_ui.json")


@pytest.fixture
def manifest():
    return json.loads(open(MANIFEST_PATH).read())


def test_mcp_template_declares_no_sidecar_and_resolves_the_config_fields():
    doc = json.loads(open(MCP_TEMPLATE_PATH).read())
    server = doc["mcpServers"]["signoz-query"]
    assert server["type"] == "http"
    assert server["url"] == "${config.query_mcp_url}"
    assert server["headers"]["SIGNOZ-API-KEY"] == "${config.query_api_key}"


def test_manifest_declares_no_mcp_sidecar(manifest):
    # This app contributes mcp.template.json but no sidecar — the design's
    # whole point is zero containers per workspace for this feature.
    sidecars = (manifest.get("runtime") or {}).get("sidecars") or []
    assert not any(s.get("name") == "mcp" for s in sidecars)


def test_config_schema_declares_the_three_new_managed_fields(manifest):
    props = manifest["config_schema"]["properties"]
    for key in ("query_mcp_url", "query_api_key", "web_ui_url"):
        assert props[key]["type"] == "string"
        assert props[key]["default"] == ""
    assert props["query_api_key"]["x-secret"] is True
    # endpoint/api_key (the existing ingest pair) are NOT marked x-secret on
    # the url side — only the credential-shaped fields are.
    assert "x-secret" not in props["query_mcp_url"]
    assert "x-secret" not in props["web_ui_url"]


def test_empty_config_disables_the_query_upstream_instead_of_401ing(tmp_path):
    pytest.importorskip(
        "src.apps.mcp_template",
        reason="requires an aw-workspace host checkout (src/apps) alongside this app repo",
    )
    import shutil

    from src.apps.mcp_template import output_path, render

    pkg = tmp_path / "signoz-connector"
    pkg.mkdir()
    shutil.copy(MCP_TEMPLATE_PATH, pkg / "mcp.template.json")

    rendered = render(str(pkg), {}, "signoz-connector")
    assert rendered["mcpServers"]["signoz-query"]["enabled"] is False

    rendered = render(
        str(pkg),
        {"query_mcp_url": "https://signoz-mcp.aw.tekflox.com/mcp",
         "query_api_key": "viewer-key-abc"},
        "signoz-connector",
    )
    assert rendered["mcpServers"]["signoz-query"]["enabled"] is True
    assert rendered["mcpServers"]["signoz-query"]["url"] == "https://signoz-mcp.aw.tekflox.com/mcp"
    assert rendered["mcpServers"]["signoz-query"]["headers"]["SIGNOZ-API-KEY"] == "viewer-key-abc"
    assert json.loads(open(output_path(str(pkg))).read()) == rendered


def test_window_spec_points_the_iframe_at_the_managed_web_ui_url():
    spec = json.loads(open(WINDOW_SPEC_PATH).read())
    widgets = spec["regions"][0]["widgets"]
    (iframe,) = [w for w in widgets if w["type"] == "iframe"]
    assert iframe["src"] == "${config.web_ui_url}"


def test_manifest_window_entry_points_at_the_spec_and_is_declarative(manifest):
    (window,) = [w for w in manifest["contributes"]["windows"] if w["id"] == "signoz-connector.ui"]
    assert window["body"]["type"] == "declarative"
    assert window["body"]["spec"] == "windows/signoz_ui.json"
