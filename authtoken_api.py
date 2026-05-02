from datetime import datetime, timedelta, timezone
from fastapi import Header
from jose import jwt
from settings import get_settings
from jose import JWTError
from fastapi import HTTPException


ALGORITHM = "HS256"


def create_token(user_id: str):
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=4)}

    settings = get_settings()

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)



def decode_token(token: str):
    try:
        settings = get_settings()
        payload = jwt.decode(token, settings.jwt_secret_key(), algorithms=[ALGORITHM])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "")
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])

        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
