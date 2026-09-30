from fastapi import Request

from app.core.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    """Expose lifespan-managed resources through FastAPI dependency injection."""
    return request.app.state.runtime
