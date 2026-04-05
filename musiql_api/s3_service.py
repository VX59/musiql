# musiql_api/s3_service.py - FIXED VERSION

import boto3
from botocore.exceptions import ClientError
from typing import Optional, List
from musiql_api.settings import get_settings
from fastapi import HTTPException

class S3Service():
    def __init__(self):
        self.settings = get_settings()
        self.bucket = self.settings.s3_bucket
        self.s3_client = boto3.client("s3", region_name=self.settings.aws_region)

    @classmethod
    def get_s3_service(cls):
        return cls()

    def object_exists(self, object_key: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def list_objects(self, prefix: str = "musiql_dump", max_keys: int = 100) -> List[str]:
        try:
            print(f"bucket {self.bucket} and prefix {prefix}")
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=max_keys
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except ClientError as e:
            print(f"Error listing objects: {e}")
            return []

    def pull_obj_stream(self, object_key: str):
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=object_key)
            file_stream = response["Body"]
            return file_stream
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                print(f"Object not found: {object_key}")
                return None
            else:
                print(f"S3 error: {e}")
                raise Exception(f"failed to fetch from S3: {str(e)}")

    def get_presigned_url(self, key:str, expires: int = 3600) -> str:
        try:
            return self.s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires
            )
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"object {key} not found, {e}")
