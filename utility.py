import secrets
from enum import Enum


def make_uri():
    uri = f"{secrets.randbelow(0x1000000):06x}"
    return uri


class AccessLevel(str, Enum):
    ADMIN = 0
    ELEVATED = 1
    STANDARD = 2
