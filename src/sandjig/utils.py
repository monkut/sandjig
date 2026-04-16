import logging

from . import settings
from .aws import SQS_CLIENT
from .jobsapi.dyanmodb.models import ProcessingJobModel, ProcessingSettingsModel

logger = logging.getLogger(__name__)


def reset_sqs_queue(queue_name: str = settings.PROCESSINGJOB_SQS_QUEUE_NAME):
    """Reset sqs queue fro given queue name"""
    assert settings.TESTING, "TESTING envar not set!"
    try:
        queue_url = SQS_CLIENT.get_queue_url(QueueName=queue_name)["QueueUrl"]
        SQS_CLIENT.delete_queue(QueueUrl=queue_url)
    except Exception:  # noqa: E722, S110, BLE001
        pass
    response = SQS_CLIENT.create_queue(QueueName=queue_name)
    return response


def reset_dynamodb():
    """Delete/Create ProcessingJobModel Table"""
    assert settings.TESTING, "TESTING envar not set!"
    if ProcessingJobModel.exists():
        logger.info("ProcessingJob.delete_table()...")
        ProcessingJobModel.delete_table()

    if ProcessingSettingsModel.exists():
        logger.info("ProcessingSettingsModel.delete_table()...")
        ProcessingSettingsModel.delete_table()

    logger.info("ProcessingJob.create_table(wait=True)...")
    ProcessingJobModel.create_table(wait=True)
