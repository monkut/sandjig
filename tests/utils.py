import datetime
import logging
from base64 import b64encode
from pathlib import Path

from sandjig import settings
from sandjig.aws import SQS_CLIENT
from sandjig.jobsapi.dyanmodb.models import ProcessingJobModel, ProcessingSettingsModel
from sandjig.models import RequestPostPayloadBaseModel, ResponsePostPayloadBaseModel, SettingsBaseModel

logger = logging.getLogger(__name__)


FIXTURES_DIRECTORY = Path(__file__).parent / "fixtures"


class TestRequestPostPayloadModel(RequestPostPayloadBaseModel):
    color: str
    value: int
    __test__ = False


class TestResponsePostPayloadModel(ResponsePostPayloadBaseModel):
    result: int
    __test__ = False


class SettingsModel(SettingsBaseModel):
    intvalue: int = 1
    strvalue: str = "value"
    datetimevalue: datetime.datetime = datetime.datetime.now()
    boolvalue: bool = False

    def model_dump(self, *args, **kwargs) -> dict:
        """Perform serialization of python datetime objects for json.dumps()."""
        d = super().model_dump(*args, **kwargs)
        d["datetimevalue"] = d["datetimevalue"].isoformat()
        return d


class BadSettingsModel(SettingsBaseModel):
    intvalue: int = 1
    requiredfield: str


def reset_sqs_queue(queue_name: str = settings.PROCESSINGJOB_SQS_QUEUE_NAME):
    try:
        queue_url = SQS_CLIENT.get_queue_url(QueueName=queue_name)["QueueUrl"]
        SQS_CLIENT.delete_queue(QueueUrl=queue_url)
    except:  # noqa: E722, S110
        pass  # noqa: S110
    response = SQS_CLIENT.create_queue(QueueName=queue_name)
    return response


def reset_dynamodb():
    """Delete/Create ProcessingJobModel Table"""
    if ProcessingJobModel.exists():
        logger.info("ProcessingJob.delete_table()...")
        ProcessingJobModel.delete_table()

    if ProcessingSettingsModel.exists():
        logger.info("ProcessingSettingsModel.delete_table()...")
        ProcessingSettingsModel.delete_table()

    logger.info("ProcessingJob.create_table(wait=True)...")
    ProcessingJobModel.create_table(wait=True)


def put_processingjobmodel_item(job_id: str = None, **kwargs) -> ProcessingJobModel:
    """Create/Put the ProcessingJobModel Item entry in the ProcessingJobModel Table"""
    item = ProcessingJobModel(job_id=str(job_id), **kwargs)
    item.save()
    return item


def get_zappa_zip_package() -> Path:
    test_zappa_zip_package_filepath = FIXTURES_DIRECTORY / "sandjig-demo-stg-1585874197.zip"
    if not test_zappa_zip_package_filepath.exists():
        import pytest

        pytest.skip("Fixture zip not available (excluded from git)")
    return test_zappa_zip_package_filepath


def get_authorization_basic_auth(username: str, password: str) -> str:
    token = b64encode(f"{username}:{password}".encode()).decode("utf-8")
    return f"Basic {token}"
