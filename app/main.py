from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api import articles, digests, sources

app = FastAPI(title="Music News Radar", version="0.1.0")

app.include_router(articles.router)
app.include_router(digests.router)
app.include_router(sources.router)

_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(_DASHBOARD, media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok"}
