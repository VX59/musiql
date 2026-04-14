from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ..media_ingestion_api.media_ingestion_api import router
import uvicorn

app = FastAPI()

app.include_router(router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8001",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def main():
    uvicorn.run(
        "media_ingestion.media_ingestion.server:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
