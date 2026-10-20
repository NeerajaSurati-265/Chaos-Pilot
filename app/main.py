from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException

from chaos_pilot.core.engine import ChaosEngine
from chaos_pilot.experiments.task_failure import ExperimentNotAllowed

app = FastAPI(
    title="Chaos-Pilot",
    description="Cloud resilience and automated remediation platform",
    version="0.1.0"
)


@lru_cache
def get_engine() -> ChaosEngine:
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


@app.get("/incidents")
def list_incidents(engine: ChaosEngine = Depends(get_engine)):
    return [incident.report() for incident in engine.store.list()]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str, engine: ChaosEngine = Depends(get_engine)):
    incident = engine.store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.report()


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
