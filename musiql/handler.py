from mangum import Mangum
from musiql.server import app
from musiql_api.musiql_api import router

app.include_router(router)
handler = Mangum(app)