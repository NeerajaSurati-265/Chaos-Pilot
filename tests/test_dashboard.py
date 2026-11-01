from pathlib import Path

from app.main import STATIC_DIR, app
from tests.test_scenario_api import make_client

JS_FILES = sorted(Path(STATIC_DIR, "js").rglob("*.js"))
JS = "\n".join(path.read_text() for path in JS_FILES)


def test_dashboard_page_is_served(monkeypatch):
    try:
        client = make_client(monkeypatch)
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        for needle in ('id="sidebar"', 'id="view"', 'id="modal-root"', "/static/js/main.js",
                       "/static/css/app.css", "/static/css/print.css", "chart.js@4.4.1",
                       "Zilla+Slab", "DM+Sans"):
            assert needle in response.text, needle
    finally:
        app.dependency_overrides.clear()


def test_static_assets_are_served(monkeypatch):
    try:
        client = make_client(monkeypatch)
        for path in ("css/tokens.css", "css/app.css", "css/print.css", "js/main.js",
                     "js/report-page.js", "report.html"):
            assert client.get(f"/static/{path}").status_code == 200, path
        assert "javascript" in client.get("/static/js/main.js").headers["content-type"]
        assert client.get("/static/css/app.css").headers["content-type"].startswith("text/css")
        assert client.get("/static/nope.js").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_every_js_file_found_and_served(monkeypatch):
    assert len(JS_FILES) >= 15
    try:
        client = make_client(monkeypatch)
        for path in JS_FILES:
            relative = path.relative_to(STATIC_DIR).as_posix()
            assert client.get(f"/static/{relative}").status_code == 200, relative
    finally:
        app.dependency_overrides.clear()


def test_dashboard_page_is_not_in_openapi(monkeypatch):
    try:
        client = make_client(monkeypatch)
        paths = client.get("/openapi.json").json()["paths"]
        assert "/" not in paths and "/reports/{incident_id}" not in paths
    finally:
        app.dependency_overrides.clear()


def test_js_never_renders_api_text_as_html():
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in JS, forbidden


def test_js_uses_the_existing_and_new_endpoints():
    for endpoint in ("/api/config", "/api/scenarios", "/incidents", "/scenarios/",
                     "/kill-switch/engage?reason=", "/kill-switch/release",
                     "/api/reports/", "/reports/"):
        assert endpoint in JS, endpoint
    assert "approve" in JS and "reject" in JS
    assert "task-failure" not in JS


def test_kill_switch_control_uses_existing_endpoints_only():
    assert "/api/kill" not in JS and "/api/switch" not in JS


def test_js_asks_for_confirmation_and_reasons():
    for text in ("Release kill switch?", "A reason is required", "Approve this fix?", "Reject this fix?"):
        assert text in JS, text


def test_js_has_honest_labels_and_hints():
    for text in ("Advice only", "Deterministic rules", "Link works on this machine while the server is running.",
                 "Cleanup NOT verified", "Coming soon", "Not recorded yet", "No incidents yet – run a scenario",
                 "Experiments disabled: set CHAOS_PILOT_EXPERIMENTS_ENABLED=true",
                 "Kill switch engaged", "Charts are unavailable"):
        assert text in JS, text


def test_js_never_claims_recovery_without_resolved_outcome():
    assert JS.count("Recovered in") == 1  # only the RESOLVED headline


def test_js_handles_every_outcome():
    for name in ("RESOLVED", "FAILED", "DENIED", "REJECTED", "ESCALATED", "ABORTED", "AWAITING_APPROVAL", "IN_PROGRESS"):
        assert name in JS, name


def test_report_includes_ai_explanation(monkeypatch):
    try:
        client = make_client(monkeypatch)
        incident_id = client.post("/scenarios/bad-deployment").json()["incident_id"]
        report = client.get(f"/incidents/{incident_id}").json()
        assert isinstance(report["explanation"], str) and report["explanation"]
        assert report["explanation"] != report["likely_cause"]
    finally:
        app.dependency_overrides.clear()


def test_report_explanation_is_none_before_diagnosis():
    from chaos_pilot.incidents.models import Incident

    assert Incident(scenario="x").report()["explanation"] is None


def test_kill_switch_flow_the_dashboard_relies_on(monkeypatch):
    try:
        client = make_client(monkeypatch)
        assert client.post("/kill-switch/engage", params={"reason": "tests & <b>stop</b>"}).json()["engaged"] is True
        assert client.get("/kill-switch").json()["reason"] == "tests & <b>stop</b>"
        refused = client.post("/scenarios/bad-deployment")
        assert refused.status_code == 403 and "Kill switch engaged" in refused.json()["detail"]
        assert client.post("/kill-switch/release").json()["engaged"] is False
    finally:
        app.dependency_overrides.clear()
