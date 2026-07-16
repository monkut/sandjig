"""Programmatic client for updating job state without the REST API (#9).

Apps that embed sandjig via :func:`sandjig.create_app` also run out-of-band
components — S3 result handlers, Batch failure handlers, workers — that must
update job state. The only supported interface has been the REST API, forcing
those in-account components to make an HTTP round-trip to their own API Gateway
URL for what is a direct DynamoDB write.

:class:`JobsClient` exposes the same update semantics as the ``/jobs/<id>``
routes (status vocabulary, error-append behaviour, timestamp handling) backed
directly by the internal pynamodb models, so REST and programmatic callers are
interchangeable and the models stay private — the models are never imported by
embedding apps, only this client is.

    from sandjig.client import JobsClient

    client = JobsClient(response_model=MyResponseModel)
    client.submit_result(job_id, {"result": 7})   # == PUT   /jobs/<id>
    client.mark_error(job_id, ["boom"])            # == PATCH /jobs/<id>
    client.set_status(job_id, "processing")        # == PATCH /jobs/<id>
    client.get_job(job_id)                          # == GET   /jobs/<id>
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from .functions import get_timestamp_now
from .jobsapi.dynamodb.models import ItemDoesNotExistError, ProcessingJobModel
from .jobsapi.validation.definitions import StatusSupportedValues, ValidPatchValues
from .models import ResponsePostPayloadBaseModel

if TYPE_CHECKING:
    from pynamodb.expressions.update import Action

logger = logging.getLogger(__name__)


class JobNotFoundError(Exception):
    """Raised when an operation targets a ``job_id`` that does not exist.

    The programmatic parity of the routes' HTTP 404 — exposed here so callers
    never import from the internal models package.
    """


class JobsClient:
    """Direct, HTTP-free surface over the ``/jobs/<id>`` update semantics.

    ``response_model`` is the same ``ResponsePostBodyModel`` subclass passed to
    :func:`sandjig.create_app`; it validates :meth:`submit_result` payloads
    exactly as the PUT route does. It is optional so callers that only change
    status (workers, failure handlers) need not supply it.
    """

    def __init__(self, response_model: type[ResponsePostPayloadBaseModel] | None = None) -> None:
        if response_model is not None:
            assert issubclass(response_model, ResponsePostPayloadBaseModel)
        self._response_model = response_model

    def get_job(self, job_id: str) -> dict:
        """Return the job's :meth:`ProcessingJobModel.as_dict` shape (== GET /jobs/<id>)."""
        return self._get_item(job_id).as_dict()

    def submit_result(self, job_id: str, response_payload: dict) -> dict:
        """Record a completed result (== PUT /jobs/<id>).

        Validates ``response_payload`` against the configured ``response_model``,
        then sets ``status=completed`` with ``completed_timestamp`` and the
        validated payload — identical to the PUT route.
        """
        if self._response_model is None:
            raise ValueError("submit_result requires a response_model; construct JobsClient(response_model=...)")
        validated = self._response_model(**response_payload)

        item = self._get_item(job_id)
        now = get_timestamp_now()
        item.update(
            actions=[
                ProcessingJobModel.status.set(StatusSupportedValues.COMPLETED.value),
                ProcessingJobModel.updated_timestamp.set(now),
                ProcessingJobModel.completed_timestamp.set(now),
                ProcessingJobModel.response_payload.set(validated.model_dump()),
            ]
        )
        return self.get_job(job_id)

    def mark_error(self, job_id: str, errors: list[str] | str) -> dict:
        """Mark the job errored, appending ``errors`` (== PATCH /jobs/<id>, status=error)."""
        return self.set_status(job_id, StatusSupportedValues.ERROR.value, errors=errors)

    def set_status(self, job_id: str, status: str, errors: list[str] | str | None = None) -> dict:
        """Transition the job's status, optionally appending ``errors`` (== PATCH /jobs/<id>).

        ``status`` must be a :class:`ValidPatchValues` member — the same
        vocabulary the PATCH route accepts. ``errors`` are appended to any
        existing list, matching the route's append (not overwrite) behaviour.
        """
        try:
            valid_status = ValidPatchValues(status).value
        except ValueError as exc:
            raise ValueError(f"invalid status '{status}'; must be one of {ValidPatchValues.values()}") from exc

        item = self._get_item(job_id)
        now = get_timestamp_now()
        actions: list[Action] = [
            ProcessingJobModel.status.set(valid_status),
            ProcessingJobModel.updated_timestamp.set(now),
        ]
        if errors is not None:
            actions.append(ProcessingJobModel.errors.set(self._append_errors(item, errors)))
        item.update(actions=actions)
        return self.get_job(job_id)

    @staticmethod
    def _append_errors(item: ProcessingJobModel, new_errors: list[str] | str) -> list[str]:
        """Append ``new_errors`` to the job's existing error list (route parity)."""
        existing = item.errors if isinstance(item.errors, list) else []
        if isinstance(new_errors, str):
            existing.append(new_errors)
        else:
            existing.extend(new_errors)
        return existing

    @staticmethod
    def _get_item(job_id: str) -> ProcessingJobModel:
        try:
            # as_dict=False always returns the model instance
            return cast(
                "ProcessingJobModel", ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=False)
            )
        except ItemDoesNotExistError as exc:
            raise JobNotFoundError(f"job_id({job_id}) not found") from exc
