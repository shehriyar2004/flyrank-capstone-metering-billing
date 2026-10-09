"""FastAPI application: routes, error envelope, background jobs."""
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import jobs
from ..config import settings
from ..db import connect, migrate
from .routes import billing, generate, health, tenants, usage, webhooks


def _envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def create_app(run_scheduler: bool = True) -> FastAPI:
    stop_event = threading.Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        migrate()
        app.state.db_factory = connect
        scheduler = None
        if run_scheduler:
            scheduler = jobs.start_scheduler(settings.job_interval_seconds, stop_event)
        yield
        stop_event.set()
        if scheduler is not None:
            scheduler.join(timeout=5)

    app = FastAPI(title="Usage Metering & Billing Engine", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Bad input -> clean 422, never a 500.
        return JSONResponse(
            status_code=422,
            content=_envelope("validation_error",
                              f"invalid request: {exc.errors()[0]['msg']}"
                              if exc.errors() else "invalid request"),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        # Last resort: JSON envelope, still no stack trace to the client.
        return JSONResponse(status_code=500,
                            content=_envelope("internal_error",
                                              "unexpected server error"))

    app.include_router(health.router)
    app.include_router(tenants.router)
    app.include_router(generate.router)
    app.include_router(usage.router)
    app.include_router(billing.router)
    app.include_router(webhooks.router)
    return app


app = create_app()


if __name__ == "__main__":
    migrate()
    uvicorn.run("src.api.app:app", host=settings.app_host, port=settings.app_port,
                log_level="info")
