import datetime
from typing import Any

from flask import Request
from flask.json.provider import DefaultJSONProvider

from .exceptions import QueryParamError


def get_datettime_range_args(request: Request) -> dict[str, float]:
    """Extract datetime range arguments from request query parameters."""
    datetime_range_args = {}
    for datetime_arg in ("registered_datetime_gte", "registered_datetime_lte"):
        # flask appears to perform unquoting on query params
        # -- not using unquote_plus here as it is not needed
        arg_value = request.args.get(datetime_arg, None)
        if arg_value:
            try:
                datetime_value = datetime.datetime.fromisoformat(arg_value)
                # convert to UTC and get timestamp (timestamps are stored internally as UTC)
                datetime_value_timestamp = datetime_value.astimezone(datetime.UTC).timestamp()
                datetime_range_args[datetime_arg] = int(datetime_value_timestamp)
            except ValueError as e:
                error_message = f'ERROR - url encoded, "{arg_value}", can not be processed as ISO-8601 datetime!'
                raise QueryParamError(message=error_message) from e
    return datetime_range_args


def strtobool(val: str | int | bool) -> bool:
    """stringをboolに変換"""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ["true", "1", "t", "y", "yes"]
    if isinstance(val, int):
        return val == 1
    return bool(val)


class CustomJSONProvider(DefaultJSONProvider):
    """Custom JSON provider to handle datetime serialization."""

    def dumps(self, obj: Any, **kwargs) -> str:  # noqa: ANN401
        """Serialize datetime objects to ISO format."""
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().dumps(obj, **kwargs)

    def default(self, obj: Any) -> str:  # noqa: ANN401
        """Handle default serialization for unsupported types."""
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)
