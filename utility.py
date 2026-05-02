import secrets
from enum import Enum
import base64

import logging

# Basic config — call this once at the top of your script/module
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def make_uri():
    uri = base64.urlsafe_b64encode(secrets.token_bytes(16)).decode().rstrip("=")
    return uri


class AccessLevel():
    ADMIN = 0
    ELEVATED = 1
    STANDARD = 2

class SourceTypes(str, Enum):
    track = "track"
    album = "album"
    playlist = "playlist"


class JobTypes(str, Enum):
    integration = "integration"
    correction = "correction"


class JobStatus(str, Enum):
    pending = "pending"
    in_progress = "in progress"
    finished = "finished"