from fastapi import FastAPI
from musiql_api.musiql_api import musiql_api_router
from musiql_api.user_management_api import user_management_router
import uvicorn

app = FastAPI()

app.include_router(musiql_api_router)
app.include_router(user_management_router)


def main():
    uvicorn.run(
        "musiql.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
