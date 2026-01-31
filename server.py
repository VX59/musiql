from fastapi import FastAPI
from musiql_api.musiql_api import router

app = FastAPI()

app.include_router(router)