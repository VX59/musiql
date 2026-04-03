# handler.py
from mangum import Mangum

from musiql.server import app  # your main FastAPI app
from musiql_api.musiql_api import router  # your router

# Include your router
app.include_router(router)

# Optional: simple root route for testing
@app.get("/")
def root():
    return {"Hello": "World"}

# --- Lambda handler ---
# IMPORTANT: set api_gateway_base_path to your stage name
handler = Mangum(app)