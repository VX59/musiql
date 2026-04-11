from fastapi import FastAPI
from ..media_ingestion_api.media_ingestion_api import router
import uvicorn

app = FastAPI()

app.include_router(router)

def main():
    uvicorn.run(
        "media_ingestion.media_ingestion.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )