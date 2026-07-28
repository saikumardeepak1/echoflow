"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

app = FastAPI(
    title="EchoFlow API",
    version="0.1.0",
    description=(
        "Voice and SMS agent platform: Twilio webhook handling, agent "
        "orchestration, and the dashboard API. This page is generated "
        "directly from the running API, so it is always current with the "
        "deployed behavior."
    ),
)

# The dashboard (apps/web) calls this API from the browser on a different
# origin (different port in local dev), so a JSON request triggers a CORS
# preflight. Without this, the browser blocks the request before it ever
# reaches a route handler.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Unauthenticated liveness check used by Docker Compose and uptime monitors."""
    return {"status": "ok"}
