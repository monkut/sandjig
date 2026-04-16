import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from sandjig import settings

INPUT_KEY_MATCH_PATTERN = r"([a-zA-Z0-9_-]+\/|)+((students|companies)_[0-9]{1,2}_(single|multiple)_[0-9]{14}.csv.gz)"


class StrEnum(str, Enum):
    """Adds .values() method to string based Enums"""

    @classmethod
    def values(cls) -> list[str]:
        return [entry.value for entry in cls]


class StatusSupportedValues(StrEnum):
    """Define the valid Patch Status Value(s)"""

    PENDING = "pending"
    QUEUED = "queued"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class ValidPatchValues(StrEnum):
    """Define the valid PATCH values."""

    PENDING = "pending"
    QUEUED = "queued"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"


class ProcessingJobPatchBody(BaseModel):
    """Validation model for ProcessingJob PATCH request body"""

    status: ValidPatchValues
    errors: list[str] | None = None


class JobsApiQueryParams(BaseModel):
    """Query Parameters validation class"""

    limit: int | None = Field(
        ge=settings.MIN_ITEMS_PER_PAGE,
        le=settings.MAX_ITEMS_PER_PAGE,
        default=settings.ITEMS_PER_PAGE,
        description="レスポンスに（１ページ）のジョブ数を指定",
    )
    job_id: list[UUID] | None = Field(
        default=None,
        description=(
            "指定している `job_id` のみをフィルターして結果を返す"
            " (最大、175の`job_id`個のフィルターは可能）"
            "例）_/jobs?job_id="
            "f02e2c3a-8de1-49af-9d0d-6220c0021999,"
            "f02e2c3a-8de1-49af-9d0d-6220c0021888,"
            "f02e2c3a-8de1-49af-9d0d-6220c0021777_"
        ),
    )
    status: StatusSupportedValues | None = Field(
        default=None, description="指定している `status` のみをフィルターして結果を返す 例）_/jobs?status=completed_"
    )

    # registered_datetime uses the same/similar value as sort_key
    # -- intended to use this value to query the ProcessingJobModel.sort_key
    registered_datetime_gte: datetime.datetime | None = Field(
        default=None,
        description="registered_datetimeが与えるURLエンコードされたISO 8601日付時間以上(>=)のジョブをフィルター",
    )
    registered_datetime_lte: datetime.datetime | None = Field(
        default=None,
        description="registered_datetimeが与えるURLエンコードされたISO 8601日付時間以下(<=)のジョブをフィルター",
    )


class BadRequest400Response(BaseModel):
    """ジョブ要求に必要なパラメータがない"""

    message: str = Field(description="エラーメッセージ内容")


class TooManyRequests429Response(BaseModel):
    """要求が多い場合返す"""

    message: str = Field(description="エラーメッセージ内容")


class NotFound404Response(BaseModel):
    """指定したjob_idは存在しません"""

    message: str = Field(description="エラーメッセージ内容")
