import os
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import analytics, incident_view
from app.catalog import build_scenarios
from app.reports import render_markdown
from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.experiments.task_failure import ExperimentNotAllowed
from chaos_pilot.policy.engine import DEFAULT_RULES

app = FastAPI(
    title="Chaos-Pilot",
    description="Cloud resilience and automated remediation platform",
    version="0.1.0"
)


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/reports/{incident_id}", include_in_schema=False)
def report_page(incident_id: str):
    # Static shell; report-page.js fetches the data and shows "not found" itself.
    return FileResponse(STATIC_DIR / "report.html")


def demo_enabled() -> bool:
    return os.getenv("CHAOS_PILOT_DEMO_DATA", "false").lower() == "true"


def is_demo(engine) -> bool:
    return getattr(engine, "demo", False) is True


@lru_cache
def get_engine() -> ChaosEngine:
    if demo_enabled():
        # Own store and pipeline, fake scenarios: the real engine is never built, so demo and
        # real incidents cannot mix and no boto3 client is created. Read once; restart to change.
        from chaos_pilot.demo.seed import build_demo_engine

        return build_demo_engine()
    return ChaosEngine()


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "chaos-pilot"
    }


@app.get("/status")
def status(engine: ChaosEngine = Depends(get_engine)):
    return engine.get_status()


@app.get("/kill-switch")
def kill_switch_status(engine: ChaosEngine = Depends(get_engine)):
    return engine.kill_switch.status()


@app.post("/kill-switch/engage")
def engage_kill_switch(
    reason: str = "engaged via API", engine: ChaosEngine = Depends(get_engine)
):
    engine.kill_switch.engage(reason)
    return engine.kill_switch.status()


@app.post("/kill-switch/release")
def release_kill_switch(engine: ChaosEngine = Depends(get_engine)):
    engine.kill_switch.release()
    return engine.kill_switch.status()


@app.post("/experiments/task-failure")
def task_failure(task_id: str, engine: ChaosEngine = Depends(get_engine)):
    try:
        return engine.run_task_failure(task_id)
    except ExperimentNotAllowed as error:
        raise HTTPException(status_code=403, detail=str(error))


@app.post("/scenarios/{name}", status_code=202)
def start_scenario(name: str, engine: ChaosEngine = Depends(get_engine)):
    try:
        incident = engine.start_scenario(name)
    except ExperimentNotAllowed as error:
        raise HTTPException(status_code=403, detail=str(error))
    return {"incident_id": incident.id, "state": incident.state.value}


def _view(engine: ChaosEngine, incident) -> dict:
    return incident_view.build(incident, engine.pipeline.approval_timeout, demo=is_demo(engine))


@app.get("/incidents")
def list_incidents(engine: ChaosEngine = Depends(get_engine)):
    return [_view(engine, incident) for incident in engine.store.list()]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    incident = engine.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _view(engine, incident)


@app.post("/incidents/{incident_id}/approve")
def approve(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    if not engine.answer_approval(incident_id, approved=True):
        raise HTTPException(status_code=409, detail="Incident is not awaiting approval")
    return {"incident_id": incident_id, "approved": True}


@app.post("/incidents/{incident_id}/reject")
def reject(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    if not engine.answer_approval(incident_id, approved=False):
        raise HTTPException(status_code=409, detail="Incident is not awaiting approval")
    return {"incident_id": incident_id, "approved": False}


@app.get("/api/config")
def api_config(engine: ChaosEngine = Depends(get_engine)):
    status = engine.get_status()
    return {
        "experiments_enabled": status["experiments_enabled"],
        "busy": status["status"] == "busy",
        "demo": is_demo(engine),
        "kill_switch": engine.kill_switch.status(),
        "workload": {
            "cluster": engine.cluster,
            "service": engine.service,
            "region": "mocked" if is_demo(engine)
            else os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None,
        },
        "approval_timeout_seconds": engine.pipeline.approval_timeout,
        "actions": {
            name: {
                "reversible": rule.reversible,
                "blast_radius": rule.blast_radius.value,
                "auto_allowed": rule.auto_allowed,
            }
            for name, rule in DEFAULT_RULES.items()
        },
    }


@app.get("/api/scenarios")
def api_scenarios(engine: ChaosEngine = Depends(get_engine)):
    return build_scenarios(engine.store.list(), set(engine.scenario_factories))


def _report_or_404(engine: ChaosEngine, incident_id: str) -> dict:
    incident = engine.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _view(engine, incident)


@app.get("/api/reports/{incident_id}.md")
def report_markdown(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    report = _report_or_404(engine, incident_id)
    return PlainTextResponse(
        render_markdown(report),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{incident_id}.md"'},
    )


@app.get("/api/reports/{incident_id}.json")
def report_json(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    return JSONResponse(
        _report_or_404(engine, incident_id),
        headers={"Content-Disposition": f'attachment; filename="{incident_id}.json"'},
    )


@app.get("/api/analytics")
def api_analytics(engine: ChaosEngine = Depends(get_engine)):
    views = [_view(engine, incident) for incident in engine.store.list()]
    return analytics.compute(views, demo=is_demo(engine))
