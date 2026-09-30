from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Error as PsycopgError
from psycopg_pool import PoolTimeout

from app.api.dependencies import get_runtime
from app.api.schemas import HealthResponse
from app.core.runtime import Runtime

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(runtime: Runtime = Depends(get_runtime)) -> HealthResponse:
    """Readiness check; response contains component status but no credentials."""
    checks: dict[str, str] = {}

    try:
        with runtime.database.connection(timeout=2) as connection:
            connection.execute("SELECT 1")
        checks["database"] = "ok"
    except (PsycopgError, PoolTimeout):
        checks["database"] = "unavailable"

    for chain_id, chain in runtime.chains.items():
        try:
            checks[f"chain:{chain_id}"] = "ok" if chain.web3.is_connected() else "unavailable"
        except Exception:
            checks[f"chain:{chain_id}"] = "unavailable"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": checks},
        )
    return HealthResponse(status="ok", checks=checks)
