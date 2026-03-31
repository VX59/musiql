from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    db_user:str
    db_password:str
    db_domain:str
    api_url:str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

@lru_cache
def get_settings() -> Settings:
    return Settings()