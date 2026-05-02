import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from musiql_api.musiql_api import musiql_api_router
from musiql_api.user_management_api import user_management_router
from musiql_api.add_music_request_api import upload_job_router
import uvicorn

app = FastAPI()

_assets_dir = "./musiql-desktop/dist/assets"
if os.path.isdir(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

app.include_router(musiql_api_router)
app.include_router(user_management_router)
app.include_router(upload_job_router)

def main():
    uvicorn.run(
        "musiql.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
