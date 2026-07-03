"""Deprecated compatibility module for :mod:`sandjig.jobsapi.dynamodb.models`."""

from __future__ import annotations

import warnings

from sandjig.jobsapi.dynamodb.models import (
    ItemDoesNotExistError,
    ProcessingJobModel,
    ProcessingSettingsModel,
    SortIndex,
    get_jsonattribute_default,
    get_sort_key,
    get_yyyymm_key,
)

warnings.warn(
    "sandjig.jobsapi.dyanmodb.models is deprecated; use "
    "sandjig.jobsapi.dynamodb.models instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ItemDoesNotExistError",
    "ProcessingJobModel",
    "ProcessingSettingsModel",
    "SortIndex",
    "get_jsonattribute_default",
    "get_sort_key",
    "get_yyyymm_key",
]
