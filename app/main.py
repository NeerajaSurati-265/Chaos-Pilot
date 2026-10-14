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
