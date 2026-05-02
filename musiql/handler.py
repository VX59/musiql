from mangum import Mangum
from musiql.server import app
from musiql_api.musiql_api import musiql_api_router
from musiql_api.user_management_api import user_management_router

app.include_router(user_management_router)
app.include_router(musiql_api_router)
handler = Mangum(app)
