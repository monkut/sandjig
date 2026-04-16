import logging
from http import HTTPStatus
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from sandjig import settings

logger = logging.getLogger(__name__)

BOTO3_CONFIG = Config(connect_timeout=settings.BOTO3_CONNECT_TIMEOUT, retries={"max_attempts": 3})

S3_CLIENT = boto3.client("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])
S3_RESOURCE = boto3.resource("s3", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["s3"])

SQS_CLIENT = boto3.client(
    "sqs", config=BOTO3_CONFIG, region_name=settings.AWS_DEFAULT_REGION, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["sqs"]
)

DYNAMODB_CLIENT = boto3.client("dynamodb", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["dynamodb"])
DYNAMODB_RESOURCE = boto3.resource("dynamodb", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["dynamodb"])

CFN_CLIENT = boto3.client("cloudformation", config=BOTO3_CONFIG, endpoint_url=settings.AWS_SERVICE_ENDPOINTS["cloudformation"])


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse s3 uri (s3://bucket/key) to (bucket, key)"""
    result = urlparse(uri)
    bucket = result.netloc
    key = result.path[1:]  # removes leading slash
    return bucket, key


def s3_key_exists(bucket: str, key: str) -> bool:
    """Check if given bucket, key exists"""
    exists = False
    try:
        S3_CLIENT.head_object(Bucket=bucket, Key=key)
        exists = True
    except ClientError as e:
        if e.response["ResponseMetadata"]["HTTPStatusCode"] == HTTPStatus.NOT_FOUND:
            logger.exception(f"s3 key does not exist: s3://{bucket}/{key}")
        else:
            logger.exception(f"Unknown ClientError: {e.args}")

    return exists
