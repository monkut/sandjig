import datetime
import logging
from collections.abc import Callable
from functools import wraps
from math import ceil
from time import sleep

from . import settings
from .models import SettingsBaseModel

logger = logging.getLogger(__name__)


def get_timestamp_now() -> float:
    """Define the default value function for dynamodb numberattribute timestamp fields"""
    return datetime.datetime.now(datetime.UTC).timestamp()


def check_model_defaults(ModelClass: type[SettingsBaseModel]):  # noqa: N803
    """Check that the user-defined settings model has defaults defined."""
    schema = ModelClass.model_json_schema()
    if "required" in schema:
        required_fieldnames = schema["required"]
        raise ValueError(f"Non-Optional, or DEFAULT not defined for field(s): {', '.join(required_fieldnames)}")


def create_dynamodb_resources(SettingsModel: type[SettingsBaseModel] | None = None) -> dict:  # noqa: N803
    """Create the resources required for dynamodb."""
    from .jobsapi.dyanmodb.models import ProcessingJobModel, ProcessingSettingsModel

    processingjobmodel_created = False
    processingsettingsmodel_created = False
    max_sleep_seconds = 30
    if not ProcessingJobModel.exists():
        logger.info("creating table %s ...", settings.DYNAMODB_PROCESSINGJOB_REQUESTS_TABLENAME)
        ProcessingJobModel.create_table()
        processingjobmodel_created = True
        logger.info("creating table %s ... DONE", settings.DYNAMODB_PROCESSINGJOB_REQUESTS_TABLENAME)
        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ...")
        sleep(settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS)
        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ... DONE")

        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ...")
        sleep(settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS)
        total_sleep_seconds = 0

        while not ProcessingJobModel.exists() and total_sleep_seconds < max_sleep_seconds:
            sleep(1)
            total_sleep_seconds += 1
        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ... DONE")

    if SettingsModel and not ProcessingSettingsModel.exists():
        logger.info("creating table %s ...", settings.DYNAMODB_PROCESSINGJOB_SETTINGS_TABLENAME)
        ProcessingSettingsModel.create_table()
        processingsettingsmodel_created = True
        logger.info("creating table %s ... DONE", settings.DYNAMODB_PROCESSINGJOB_SETTINGS_TABLENAME)

        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ...")
        sleep(settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS)
        total_sleep_seconds = 0
        while not ProcessingSettingsModel.exists() and total_sleep_seconds < max_sleep_seconds:
            sleep(1)
            total_sleep_seconds += 1
        logger.info(f"Waiting {settings.CREATE_DYNAMODB_RESOURCES_WAIT_SECONDS}s for creation to complete ... DONE")

        logger.info("Creating initial SettingsModel/ProcessingSettingsModel Entry...")
        settings_defaults = SettingsModel()
        item = ProcessingSettingsModel(
            settings_id=settings.SETTINGS_ID,
            updated_timestamp=get_timestamp_now(),
            settings=settings_defaults.model_dump(),
        )
        item.save()
        logger.info("Creating initial SettingsModel/ProcessingSettingsModel Entry... DONE")

    return {
        "ProcessingJobModel": processingjobmodel_created,
        "ProcessingSettingsModel": processingsettingsmodel_created,
    }


def noauthdecorator(function: Callable) -> Callable:
    """Create a passthrough no-auth decorator applied only when basicauth is *not* used"""

    @wraps(function)
    def decorated_function(*args, **kwargs):  #  noqa: ANN202
        return function(*args, **kwargs)

    return decorated_function


def mask_string(s: str, perc: float = 0.6, mask_character: str = "*") -> str:
    """Mask a given string by the defined percentage."""
    mask_chars = ceil(len(s) * perc)
    return f"{mask_character * mask_chars}{s[mask_chars:]}"
