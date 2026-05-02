import boto3
from botocore.exceptions import ClientError
from typing import List
from settings import get_settings
from fastapi import HTTPException
import os
from sqlalchemy.future import select
from database.models import MusiqlRepository
from sqlalchemy.orm import sessionmaker
from functools import lru_cache
from utility import logger
from botocore.exceptions import ClientError, EndpointResolutionError


class DuplicateResource(Exception):
    pass


async def resource_exists(hash: bytes, session_maker: sessionmaker) -> bool:
    stmt = select(MusiqlRepository).where(MusiqlRepository.hash == hash)

    async with session_maker() as session:
        result = await session.execute(stmt)
        record = result.scalars().first()
        if record is not None:
            return True

    return False


class SQS:
    def __init__(self):
        self.settings = get_settings()
        self.sqs_client = boto3.client("sqs", region_name=self.settings.aws_region)

        response = self.sqs_client.get_queue_url(QueueName="RecordingServerQueue")
        self.queue_url = response["QueueUrl"]

    def send_message(self, body, attributes=None):
        try:
            params = {
                'QueueUrl': self.queue_url,
                'MessageBody': body,
            }
            if attributes:
                params['MessageAttributes'] = attributes

            response = self.sqs_client.send_message(**params)
            message_id = response['MessageId']
            logger.info('Message sent successfully. MessageId: %s', message_id)
            return message_id

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']

            if error_code == 'InvalidMessageContents':
                logger.error('Message body contains invalid characters: %s', error_msg)
            elif error_code == 'UnsupportedOperation':
                logger.error('Operation not supported on this queue: %s', error_msg)
            elif error_code == 'AccessDenied':
                logger.error('IAM permissions error — check your SQS policy: %s', error_msg)
            else:
                logger.error('Unexpected SQS error [%s]: %s', error_code, error_msg)

            raise  # re-raise so the caller knows it failed

        except EndpointResolutionError:
            logger.error('Could not resolve SQS endpoint — check your region config')
            raise

        except Exception as e:
            logger.exception('Unexpected error sending message')
            raise
    
    def receive_message(self):
        try:
            response = self.sqs_client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=20,
                MessageAttributeNames=["All"]
            )

            messages = response.get("Messages", [])
            return messages
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "AWS.SimpleQueueService.NonExistentQueue":
                logger.error("SQS queue does not exist: %s", error_msg)
            elif error_code == "OverLimit":
                logger.error("SQS receive limit exceeded: %s", error_msg)
            elif error_code == "AccessDenied":
                logger.error("IAM permissions error — check your SQS policy: %s", error_msg)
            else:
                logger.error("Unexpected SQS error [%s]: %s", error_code, error_msg)

            raise

        except EndpointResolutionError:
            logger.error("Could not resolve SQS endpoint — check your region config")
            raise

        except Exception:
            logger.exception("Unexpected error receiving messages")
            raise
    
    def delete_message(self, receipt_handle):
        try:
            self.sqs_client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle
            )
            logger.info("Message deleted successfully. ReceiptHandle: %s", receipt_handle)

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]

            if error_code == "AWS.SimpleQueueService.NonExistentQueue":
                logger.error("SQS queue does not exist: %s", error_msg)
            elif error_code == "ReceiptHandleIsInvalid":
                logger.error("Invalid receipt handle — message may have already been deleted or expired: %s", error_msg)
            elif error_code == "AccessDenied":
                logger.error("IAM permissions error — check your SQS policy: %s", error_msg)
            else:
                logger.error("Unexpected SQS error [%s]: %s", error_code, error_msg)

            raise

        except EndpointResolutionError:
            logger.error("Could not resolve SQS endpoint — check your region config")
            raise

        except Exception:
            logger.exception("Unexpected error deleting message")
            raise
        

@lru_cache
def get_SQS() -> SQS:
    return SQS()


class S3:
    def __init__(self):
        self.settings = get_settings()
        self.bucket = self.settings.s3_bucket
        self.s3_client = boto3.client("s3", region_name=self.settings.aws_region)
        self.chunk_size = 5 * 1024 * 1024

    def object_exists(self, object_key: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket, Key=object_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def list_objects(
        self, prefix: str = "musiql_dump", max_keys: int = 100
    ) -> List[str]:
        try:
            logger.info(f"bucket {self.bucket} and prefix {prefix}")
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=max_keys
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except ClientError as e:
            logger.exception(f"Error listing objects: {e}")
            return []

    def pull_obj_stream(self, object_key: str):
        try:
            response = self.s3_client.get_object(Bucket=self.bucket, Key=object_key)
            file_stream = response["Body"]
            return file_stream
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "NoSuchKey":
                raise KeyError(f"Object not found: {object_key}")
            else:
                logger.exception(f"S3 error: {e}")
                raise Exception(f"failed to fetch from S3: {str(e)}")

    def put_object(self, data, key):
        self.s3_client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_presigned_url(self, key: str, expires: int = 3600) -> str:
        try:
            return self.s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires,
            )
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"object {key} not found, {e}")

    def delete_object(self, key):
        self.s3_client.delete_object(Bucket=self.bucket, Key=key)


    def commit_multipart_upload(self, key, upload_id, parts, obj_path):
        self.s3_client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        os.remove(obj_path)


    def upload_object_from_path(self, obj_path, key):
        mpu = self.s3_client.create_multipart_upload(Bucket=self.bucket, Key=key)

        upload_id = mpu["UploadId"]

        parts = []
        part_number = 1

        with open(obj_path, "rb") as reader:
            try:
                while chunk := reader.read(self.chunk_size):
                    part = self.s3_client.upload_part(
                        Bucket=self.bucket,
                        Key=key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk,
                    )

                    parts.append({"PartNumber": part_number, "ETag": part["ETag"]})

                    part_number += 1

                return upload_id, parts
            except Exception as e:
                # Abort on failure
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket, Key=key, UploadId=upload_id
                )
                raise e


@lru_cache
def get_S3() -> S3:
    return S3()
