from pydantic_settings import BaseSettings
from functools import lru_cache
import json
import os
import boto3
from botocore.config import Config


class Settings(BaseSettings):
    db_user: str
    db_name: str
    db_port: str
    db_password: str
    db_domain: str
    musiql_api_url: str
    jwt_secret_key: str

    aws_region: str = "us-east-2"
    s3_bucket: str = "musiql-s3-bucket"

    spotify_client_id: str
    spotify_client_secret: str

    env: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def get_secret():
    secret_arn = os.getenv("SECRET_ARN", "musiql/db-credentials")
    region_name = "us-east-2"

    try:
        session = boto3.session.Session()
        client = session.client(
            service_name="secretsmanager",
            region_name=region_name,
            config=Config(read_timeout=5, connect_timeout=5),
        )

        resp = client.get_secret_value(SecretId=secret_arn)
        secret = json.loads(resp["SecretString"])
        print("DEBUG: secret loaded:", secret)
        return secret
    except Exception as e:
        print("ERROR fetching secret:", e)
        return None


@lru_cache
def get_settings() -> Settings:
    env = os.getenv("ENV", "localhost")
    print("ENV:", env)

    if env != "localhost":
        secret = get_secret()
        if secret is None:
            raise Exception("unable to fetch secrets")

        sensitive_fields = {
            "db_user",
            "db_password",
            "db_domain",
            "jwt_secret_key",
            "spotify_client_id",
            "spotify_client_secret",
            "musiql_api_url",
        }
        sensitive = {k: v for k, v in secret.items() if k in sensitive_fields}
        print("DEBUG: passing to Settings:", sensitive)
        return Settings(**sensitive)

    else:
        print("Not deployed")
        return Settings()
