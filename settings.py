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
    media_ingestion_api_url: str
    jwt_secret_key: str

    aws_region: str = "us-east-2"
    s3_bucket: str = "musiql-s3-bucket"
    env: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def get_secret():
    secret_name = "musiql/db-credentials"
    region_name = "us-east-2"

    try:
        session = boto3.session.Session()
        client = session.client(
            service_name="secretsmanager",
            region_name=region_name,
            config=Config(read_timeout=5, connect_timeout=5),  # short timeout
        )

        resp = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(resp["SecretString"])
        print("DEBUG: secret loaded:", secret)
        return secret
    except Exception as e:
        print("ERROR fetching secret:", e)
        return None


@lru_cache
def get_settings() -> Settings:
    env = os.getenv("ENV", "dev")
    print("ENV:", env)

    if env == "production":
        secret = get_secret()
        if secret is None:
            print("WARNING: Could not fetch secrets, using dummy defaults")
            secret = {
                "db_user": "dummy",
                "db_name": "dummy",
                "db_port": "5432",
                "db_password": "dummy",
                "db_domain": "dummy",
                "api_url": "http://localhost:8000",
                "env": "production",
            }

        print("DEBUG: passing to Settings:", secret)
        settings = Settings(**secret)
        settings.aws_region = os.getenv("AWS_REGION", "us-east-2")
        settings.s3_bucket = os.getenv("S3_BUCKET", "musiql-s3-bucket")
        return settings

    else:
        print("No secrets loaded because not in production")
        return Settings()
