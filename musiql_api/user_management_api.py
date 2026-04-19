from fastapi import APIRouter, HTTPException, status, Depends
from database.db import get_session
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
import hashlib
from database.models import Users
from utility import make_uri
from authtoken_api import create_token, get_current_user

user_management_router = APIRouter()


class CreateUserPayload(BaseModel):
    username: str
    password: str
    access_level: int


class LoginUserPayload(BaseModel):
    username:str
    password:str


@user_management_router.post("/musiql/login/user", response_model=None)
async def user_login(
    payload:LoginUserPayload,
    session_maker: sessionmaker = Depends(get_session),
):
    
    pword_hash = hashlib.sha256(payload.password.encode("utf-8")).digest()

    stmt = select(Users.uri).where(
        Users.username == payload.username,
        Users.password == pword_hash
    )

    async with session_maker() as session:
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=401, detail="Invalid credentials"
            )

        token = create_token(user)

        return {"token": token}


@user_management_router.post("/musiql/create/user", response_model=None)
async def create_user(
    payload: CreateUserPayload,
    session_maker: sessionmaker = Depends(get_session),
    user_id = Depends(get_current_user)
    
):
    uri = f"{payload.username}-{make_uri()}"

    try:
        new_user = Users(
            uri=uri,
            username=payload.username,
            password=hashlib.sha256(payload.password.encode("utf-8")).digest(),
            access_level = payload.access_level
        )

        async with session_maker() as session, session.begin():
            session.add(new_user)

        return JSONResponse(
            content=f"Succesfully created user {payload.username} with {payload.access_level} access"
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )