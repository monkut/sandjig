"""Deprecated compatibility package for :mod:`sandjig.jobsapi.dynamodb`."""

from __future__ import annotations

import warnings

warnings.warn(
    "sandjig.jobsapi.dyanmodb is deprecated; use sandjig.jobsapi.dynamodb instead.",
    DeprecationWarning,
    stacklevel=2,
)
