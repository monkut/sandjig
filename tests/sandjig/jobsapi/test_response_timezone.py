"""RESPONSE_TIMEZONE controls the offset applied to JobResponse datetimes (#5).

as_dict() converts stored UTC timestamps to ISO-8601 in a configurable zone.
Default is Asia/Tokyo (+09:00) for backwards compatibility; deployments outside
Japan (e.g. sanji on us-west-2) set RESPONSE_TIMEZONE to their own zone.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from sandjig import settings
from sandjig.jobsapi.dynamodb.models import ProcessingJobModel

# 2026-01-02T03:04:05Z — a fixed instant so the offset is the only variable
FIXED_TS = 1767322745


def _job() -> ProcessingJobModel:
    return ProcessingJobModel(
        job_id="tz-test",
        request_payload={"color": "purple", "value": 1},
        registered_timestamp=FIXED_TS,
        updated_timestamp=FIXED_TS,
        completed_timestamp=FIXED_TS,
    )


def test_default_timezone_is_jst():
    """Unset RESPONSE_TIMEZONE keeps the historical +09:00 offset."""
    item = _job().as_dict()
    assert item["registered_datetime"].endswith("+09:00")
    assert item["completed_datetime"].endswith("+09:00")


def test_response_timezone_utc(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "RESPONSE_TIMEZONE", ZoneInfo("UTC"))
    item = _job().as_dict()
    assert item["registered_datetime"].endswith("+00:00")


def test_response_timezone_us_west(monkeypatch: pytest.MonkeyPatch):
    # America/Los_Angeles is -08:00 in January (PST, no DST)
    monkeypatch.setattr(settings, "RESPONSE_TIMEZONE", ZoneInfo("America/Los_Angeles"))
    item = _job().as_dict()
    assert item["registered_datetime"].endswith("-08:00")


def test_response_timezone_matches_same_instant():
    """Changing the zone re-labels the same instant, never shifts it."""
    jst = _job().as_dict()["registered_datetime"]
    # JST-rendered value, parsed back and normalized to UTC, equals the source instant
    assert int(datetime.fromisoformat(jst).timestamp()) == FIXED_TS
