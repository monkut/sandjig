"""Model definitions for data stored in Dynamodb"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from pynamodb import __version__ as pynamodb_version
from pynamodb.attributes import JSONAttribute, NumberAttribute, TTLAttribute, UnicodeAttribute
from pynamodb.expressions.condition import Condition
from pynamodb.indexes import AllProjection, GlobalSecondaryIndex
from pynamodb.models import Model

from ... import settings
from ...functions import get_timestamp_now

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)
logger.debug(f"pynamodb_version: {pynamodb_version}")


MIN_SORT_HASHKEY = -202412
logger.debug(f"MIN_SORT_HASHKEY={MIN_SORT_HASHKEY}")


class ItemDoesNotExistError(Exception):
    """Exception raised when a query for a specific job_id is made and an entry does not exist"""

    # pylint disable=unnecessary-pass


def get_sort_key() -> float:
    """
    Define the value to be used for the sort key to ensure newest-> oldest order on table scan
    NOTE: if raw (float) timestamp is used, we may not be able to properly apply range queries
    """
    return -int(datetime.datetime.now(datetime.UTC).timestamp())


def get_yyyymm_key(ts: int | None = None) -> int:
    """Provide the default value for the SortIndex hash_key"""
    if ts is not None:
        # convert ts to yyyymm hashkey format
        start_of_month = (
            datetime.datetime.fromtimestamp(ts).astimezone(datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        )
    else:
        start_of_month = datetime.datetime.now(datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    yyyymm = start_of_month.strftime("%Y%m")
    return -int(yyyymm)


class SortIndex(GlobalSecondaryIndex):
    """Define the index on ProcessingJobModel.registered_timestamp in order to return paged sorted results"""

    class Meta:
        # index_name is optional, but can be provided to override the default name
        index_name = settings.DYNAMODB_SORTINDEX_INDEXNAME
        region = settings.AWS_DEFAULT_REGION
        host = settings.AWS_SERVICE_ENDPOINTS["dynamodb"]
        read_capacity_units = settings.SORTINDEX_READ_CAPACITY_UNITS
        write_capacity_units = settings.SORTINDEX_WRITE_CAPACITY_UNITS
        projection = AllProjection()

    # This attribute is the hash key for the index
    # Note that this attribute must also exist
    # in the model
    yyyymm = NumberAttribute(hash_key=True)
    sort_key = NumberAttribute(range_key=True)


class ProcessingJobModel(Model):
    """Main dynamodb model for managing request data"""

    if settings.RECORD_TTL_DAYS is not None:
        ttl = TTLAttribute(default_for_new=datetime.timedelta(days=settings.RECORD_TTL_DAYS))

    job_id = UnicodeAttribute(hash_key=True)
    yyyymm = NumberAttribute(default=get_yyyymm_key)
    sort_key = NumberAttribute(default=get_sort_key)
    status = UnicodeAttribute(default=settings.PROCESSINGJOB_STATUS_DEFAULT)
    predictor_status = UnicodeAttribute(null=True)
    registered_timestamp = NumberAttribute(default=get_timestamp_now)
    updated_timestamp = NumberAttribute(default=get_timestamp_now)
    completed_timestamp = NumberAttribute(null=True)
    settings = JSONAttribute(null=True)
    request_payload = JSONAttribute()
    response_payload = JSONAttribute(null=True)
    errors = JSONAttribute(null=True)

    sort_index = SortIndex()

    def as_dict(self) -> dict[str, int | float | str | None]:
        """Take the current model and reviews the attributes to then translate to a dict"""
        result_dict: dict[str, int | float | str | None] = {}
        ignore_keys = ("yyyymm", "sort_key", "ttl")
        for attr in self.get_attributes().keys():
            if attr in ignore_keys:
                continue

            if attr.endswith("_timestamp"):
                # convert timestamp to datetime iso8601
                value = getattr(self, attr)
                key = attr.replace("_timestamp", "_datetime")
                if value:
                    utc = datetime.datetime.fromtimestamp(value, tz=datetime.UTC)
                    jst = utc.astimezone(settings.JST)
                    jst = jst.replace(microsecond=0)  # remove microseconds portion
                    result_dict[key] = jst.isoformat()
                else:
                    result_dict[key] = None
            else:
                result_dict[attr] = getattr(self, attr)

        # replace settings {} with None -- fails JSON validation if {}
        if not result_dict["settings"] and isinstance(result_dict["settings"], dict):
            result_dict["settings"] = None
        if "predictor_status" in result_dict and result_dict["predictor_status"] and result_dict["status"] != "cancelled":
            result_dict["status"] = result_dict["predictor_status"]
        if "predictor_status" in result_dict:
            result_dict.pop("predictor_status")
        result_dict["result_count"] = 0
        return result_dict

    @classmethod
    def get_processingjobmodel_item(cls, job_id: str | UUID, as_dict: bool = True) -> dict | ProcessingJobModel:
        """Return single item matching job_id"""
        query_results = list(ProcessingJobModel.query(str(job_id)))
        if query_results:
            assert len(query_results) == 1
            job = query_results[0]
            if as_dict:
                return job.as_dict()
            return job
        raise ItemDoesNotExistError(f"ProcessingJobModel(job_id={job_id}) not found!")

    @classmethod
    def get_processingjobmodel_items(cls, job_ids: list[str | UUID], as_dict: bool = True) -> list[dict | ProcessingJobModel]:
        """Return items matching given job_ids"""
        normalized_job_ids = [str(job_id) for job_id in job_ids]
        results = ProcessingJobModel.batch_get(normalized_job_ids)
        if results:
            if as_dict:
                return [item.as_dict() for item in results]
            return list(results)
        return []

    class Meta:
        table_name = settings.DYNAMODB_PROCESSINGJOB_REQUESTS_TABLENAME
        region = settings.AWS_DEFAULT_REGION
        host = settings.AWS_SERVICE_ENDPOINTS["dynamodb"]
        read_capacity_units = settings.DYNAMODB_PROCESSINGJOB_REQUESTS_READ_CAPACITY_UNITS
        write_capacity_units = settings.DYNAMODB_PROCESSINGJOB_REQUESTS_WRITE_CAPACITY_UNITS

    @classmethod
    def _hash_key_is_valid(cls, hash_key: int, gte_value: int | None = None, lte_value: int | None = None) -> bool:
        """Check if the hash_key is valid based on gte_value and lte_value"""

        def _yyyymm(value: int) -> int:
            """Convert value to yyyymm hashkey format"""
            dt = datetime.datetime.fromtimestamp(value, tz=datetime.UTC)
            yyyymm = int(dt.strftime("%Y%m"))
            return -yyyymm

        # numbers are inverted to negative yyyymm format
        if gte_value is not None and hash_key > _yyyymm(gte_value):  # noqa: SIM103
            return False
        if lte_value is not None and hash_key < _yyyymm(lte_value):  # noqa: SIM103
            return False
        return True  # noqa: SIM103

    @classmethod
    def _convert_ts_to_sortkey_value(cls, ts: float) -> int | None:
        """Convert a timestamp to a sort key value"""
        if ts:
            if isinstance(ts, float):
                ts = int(ts)
            return -ts
        return None

    @classmethod
    def _query_page(
        cls,
        hash_key: int,
        last_evaluated_key: str | None = None,
        status_filter: Condition | None = None,
        between_condition: Condition | None = None,
        range_condition: Condition | None = None,
        limit: int = 10,
        as_dict: bool = True,
    ) -> tuple[list[dict | ProcessingJobModel], dict | None]:
        if status_filter and between_condition is not None:
            logger.debug("using status_filter and between_condition in index.query()...")
            results = cls.sort_index.query(
                hash_key,
                range_key_condition=between_condition,
                filter_condition=(cls.status == status_filter),
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
        elif status_filter and range_condition is not None:
            logger.debug("using status_filter and range_condition in index.query()...")
            results = cls.sort_index.query(
                hash_key,
                range_key_condition=range_condition,
                filter_condition=(cls.status == status_filter),
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
        elif status_filter and range_condition is None and between_condition is None:
            logger.debug("using status_filter in index.query()...")
            results = cls.sort_index.query(
                hash_key,
                range_key_condition=range_condition,
                filter_condition=(cls.status == status_filter),
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            )
        elif between_condition is not None:
            logger.debug("using between_condition in index.query()...")
            logger.debug(f"between_condition={between_condition}")
            results = cls.sort_index.query(
                hash_key, range_key_condition=between_condition, limit=limit, last_evaluated_key=last_evaluated_key
            )
        elif range_condition is not None:
            logger.debug("using range_condition in index.query()...")
            logger.debug(f"range_condition={range_condition}")
            results = cls.sort_index.query(
                hash_key, range_key_condition=range_condition, limit=limit, last_evaluated_key=last_evaluated_key
            )
        else:
            logger.debug("no status_filter or range_condition applied to index.query()...")
            results = cls.sort_index.query(hash_key, limit=limit, last_evaluated_key=last_evaluated_key)
        last_evaluated_key = results.last_evaluated_key
        return [result.as_dict() if as_dict else result for result in results], last_evaluated_key

    @classmethod
    def get_paged_results(
        cls,
        last_evaluated_key: dict | None = None,
        status_filter: str | None = None,
        limit: int = 10,
        as_dict: bool = True,
        **kwargs: Any,  # noqa: ANN401
    ) -> tuple[list[dict | ProcessingJobModel], dict | None]:
        """Return paged results"""
        logger.debug(f"settings.DYNAMODB_SORTINDEX_INDEXNAME={settings.DYNAMODB_SORTINDEX_INDEXNAME}")
        logger.debug(f"last_evaluated_key={last_evaluated_key}")

        range_condition = None
        between_condition = None
        # check/prepare gte value
        gte_value: int | None = kwargs.get("registered_datetime_gte")
        sort_key_gte_value = cls._convert_ts_to_sortkey_value(ts=gte_value)
        if sort_key_gte_value:
            range_condition = cls.sort_key <= sort_key_gte_value

        # check/prepare lte value
        lte_value: int | None = kwargs.get("registered_datetime_lte")
        sort_key_lte_value = cls._convert_ts_to_sortkey_value(ts=lte_value)
        if sort_key_lte_value and range_condition is None:
            range_condition = cls.sort_key >= sort_key_lte_value
        elif range_condition is not None and sort_key_gte_value is not None and sort_key_lte_value is not None:
            logger.debug("combining sort_key_gte_value and sort_key_lte_value into range_condition...")
            # -- values are inverted to the negative
            between_condition = cls.sort_key.between(sort_key_lte_value, sort_key_gte_value)
        logger.debug(f"range_condition={range_condition}")

        if not last_evaluated_key:
            # if gte_value is given, use that was the 'start' hash_key value
            hash_key = get_yyyymm_key(ts=gte_value)  # gte_value can be None
        else:
            str_value = last_evaluated_key["yyyymm"]["N"]
            hash_key = int(str_value)

        page_results = []
        while len(page_results) < limit:
            logger.debug(f"hash_key={hash_key}, results.last_evaluated_key={last_evaluated_key}")
            results, last_evaluated_key = cls._query_page(
                hash_key,
                last_evaluated_key,
                status_filter,
                between_condition,
                range_condition,
                limit=limit,
                as_dict=as_dict,
            )
            page_results.extend(results)

            if last_evaluated_key is not None:
                break
            if hash_key > MIN_SORT_HASHKEY:
                logger.debug(f"hash_key({hash_key}) > MIN_SORT_HASHKEY({MIN_SORT_HASHKEY}) breaking...")
                break
            # build the previous month hash_key
            current_hash_key_datetime = datetime.datetime.strptime(str(hash_key)[1:], "%Y%m")  # noqa: DTZ007
            previous_month = (current_hash_key_datetime - datetime.timedelta(days=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            hash_key = -int(previous_month.strftime("%Y%m"))
            logger.debug(f"updating hash_key to previous month -> {hash_key}")

            # if gte/lte values are provided, check if the hash_key is valid
            # -- all hash_keys will be searched until the MIN_SORT_HASHKEY is reached
            if gte_value and not cls._hash_key_is_valid(hash_key, gte_value=gte_value):
                logger.debug(f"hash_key({hash_key}) is less than gte_value({gte_value}), breaking...")
                break
            if lte_value and not cls._hash_key_is_valid(hash_key, lte_value=lte_value):
                logger.debug(f"hash_key({hash_key}) is greater than lte_value({lte_value}), breaking...")
                break

            logger.debug(f"(next) hash_key={hash_key}")

        return page_results, last_evaluated_key

    def get_sqs_message(self, as_json: bool = True) -> dict | str:
        """Convert entry to ProcessingJob Request message format"""
        message: dict[str, int | float | str | None] | str = {}
        ignore_keys = ("sort_key", "status", "predictor_status", "yyyymm")
        for attr in self.get_attributes().keys():
            if attr in ignore_keys:
                continue
            message[attr] = getattr(self, attr)

        if as_json:
            message = json.dumps(message)
        return message


def get_jsonattribute_default() -> dict:
    return {}


class ProcessingSettingsModel(Model):
    """Main dynamodb model for managing request data"""

    settings_id = UnicodeAttribute(hash_key=True)
    updated_timestamp = NumberAttribute(default=get_timestamp_now)
    settings = JSONAttribute(default=get_jsonattribute_default)

    @classmethod
    def get_processingsettingsmodel_item(cls, settings_id: str | UUID, as_dict: bool = True) -> dict | ProcessingSettingsModel:
        """Return single item matching settings_id"""
        query_results = list(ProcessingSettingsModel.query(str(settings_id)))
        if query_results:
            assert len(query_results) == 1
            item = query_results[0]
            if as_dict:
                return item.as_dict()
            return item
        raise ItemDoesNotExistError(f"ProcessingSettingsModel(settings_id={settings_id}) not found!")

    def as_dict(self) -> dict[str, int | float | str | None]:
        """Take the current model and reviews the attributes to then translate to a dict"""
        result_dict: dict[str, dict | int | float | str | None] = {}
        for attr in self.get_attributes().keys():
            if attr.endswith("_timestamp"):
                # convert timestamp to datetime iso8601
                value = getattr(self, attr)
                key = attr.replace("_timestamp", "_datetime")
                if value:
                    utc = datetime.datetime.fromtimestamp(value, tz=datetime.UTC)
                    jst = utc.astimezone(settings.JST)
                    jst = jst.replace(microsecond=0)  # remove microseconds portion
                    result_dict[key] = jst.isoformat()
                else:
                    result_dict[key] = None
            elif attr == "settings_id":
                continue  # user doesn't need to know this settings_id
            else:
                result_dict[attr] = getattr(self, attr)
        # move "settings" to root
        user_settings = result_dict.pop("settings")
        result_dict.update(user_settings)
        return result_dict

    class Meta:
        table_name = settings.DYNAMODB_PROCESSINGJOB_SETTINGS_TABLENAME
        region = settings.AWS_DEFAULT_REGION
        host = settings.AWS_SERVICE_ENDPOINTS["dynamodb"]
        read_capacity_units = settings.DYNAMODB_PROCESSINGJOB_SETTINGS_READ_CAPACITY_UNITS
        write_capacity_units = settings.DYNAMODB_PROCESSINGJOB_SETTINGS_WRITE_CAPACITY_UNITS
