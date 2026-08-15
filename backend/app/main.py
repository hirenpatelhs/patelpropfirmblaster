from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import auth, resources, stubs, system
from app.core.config import settings


Path("logs").mkdir(exist_ok=True)
structlog.configure(processors=[structlog.processors.TimeStamper(fmt="iso", utc=True), structlog.processors.add_log_level, structlog.processors.JSONRenderer()])
logger = structlog.get_logger()

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment, traces_sample_rate=0.05)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path("data/telegram").mkdir(parents=True, exist_ok=True)
    logger.info("application_started", environment=settings.environment)
    yield
    logger.info("application_stopped")


docs_url = "/docs" if settings.docs_enabled or not settings.is_production else None
app = FastAPI(
    title="Patel Propfirm Blaster API",
    description="Prop-firm signal execution and deterministic risk-control API.",
    version="1.0.0",
    docs_url=docs_url,
    redoc_url=None if docs_url is None else "/redoc",
    openapi_url=None if docs_url is None else "/openapi.json",
    lifespan=lifespan,
)
app.state.limiter = auth.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception", path=request.url.path, method=request.method, exception_type=type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "An internal error occurred"})


app.include_router(auth.router, prefix="/api/v1")
app.include_router(resources.accounts_router, prefix="/api/v1")
app.include_router(resources.firms_router, prefix="/api/v1")
app.include_router(resources.rules_router, prefix="/api/v1")
app.include_router(resources.sources_router, prefix="/api/v1")
app.include_router(resources.signals_router, prefix="/api/v1")
app.include_router(resources.trades_router, prefix="/api/v1")
app.include_router(resources.audit_router, prefix="/api/v1")
app.include_router(stubs.orders_router, prefix="/api/v1")
app.include_router(stubs.positions_router, prefix="/api/v1")
app.include_router(stubs.risk_router, prefix="/api/v1")
app.include_router(stubs.analytics_router, prefix="/api/v1")
app.include_router(stubs.settings_router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "Patel Propfirm Blaster"}
