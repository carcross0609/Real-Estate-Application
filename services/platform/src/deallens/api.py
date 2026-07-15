"""FastAPI app assembly — mounts every module's router. See §12.1: OpenAPI-first, CI
generates the typed TS client from the spec this app serves at `/openapi.json`.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deallens.core.config import get_settings
from deallens.core.errors import register_error_handlers
from deallens.core.logging import configure_logging
from deallens.modules.engine.router import router as engine_router
from deallens.modules.identity.router import router as identity_router
from deallens.modules.identity.webhooks import router as identity_webhooks_router
from deallens.modules.scoring.router import router as scoring_router
from deallens.modules.search.router import router as search_router
from deallens.modules.vision.router import router as vision_router

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(title="DealLens API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_error_handlers(app)

app.include_router(identity_router, prefix="/v1")
app.include_router(search_router, prefix="/v1")
app.include_router(engine_router, prefix="/v1")
app.include_router(vision_router, prefix="/v1")
app.include_router(scoring_router, prefix="/v1")
app.include_router(identity_webhooks_router, prefix="/webhooks")


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
