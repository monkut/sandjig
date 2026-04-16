import datetime

from pydantic import BaseModel, ConfigDict


class RequestPostPayloadBaseModel(BaseModel):
    """Pydantic model to subclass for defining the Request Payload."""


class ResponsePostPayloadBaseModel(BaseModel):
    """Pydantic model to subclass for defining the Response Payload."""


class SettingsBaseModel(BaseModel):
    """Pydantic model to subclass for defining settings"""

    model_config = ConfigDict(extra="forbid")  # Forbid extra fields

    updated_datetime: datetime.datetime | None = None
