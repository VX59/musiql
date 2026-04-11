from mangum import Mangum
from media_ingestion.server import app 
from media_ingestion_api.media_ingestion_api import router

app.include_router(router)
handler = Mangum(app)