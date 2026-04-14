from fastapi import FastAPI
from musiql_api.musiql_api import router
import uvicorn

app = FastAPI()

app.include_router(router)


def main():
    uvicorn.run(
        "musiql.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
