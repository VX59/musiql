from mangum import Mangum
from musiql.server import app

handler = Mangum(app)
