from fastapi import FastAPI

from app.api import articles, digests

app = FastAPI(title="Music News Radar", version="0.1.0")

app.include_router(articles.router)
app.include_router(digests.router)


@app.get("/health")
def health():
    return {"status": "ok"}
