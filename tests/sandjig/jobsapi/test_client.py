"""Tests for the sandjig.client programmatic surface (#9).

The client mirrors the REST route semantics (PUT/PATCH/GET on /jobs/<id>)
without an HTTP round-trip, so embedding apps' out-of-band components
(S3 result handlers, Batch failure handlers, workers) can update job state
by a direct DynamoDB write. Parity with the routes is asserted directly.
"""

from http import HTTPStatus
from unittest import TestCase
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sandjig import create_app
from sandjig.client import JobNotFoundError, JobsClient
from sandjig.jobsapi.validation.definitions import StatusSupportedValues

from ...utils import (
    TestRequestPostPayloadModel,
    TestResponsePostPayloadModel,
    put_processingjobmodel_item,
    reset_dynamodb,
    reset_sqs_queue,
)


class JobsClientTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()
        self.client = JobsClient(response_model=TestResponsePostPayloadModel)

    def _seed_job(self, status: str = StatusSupportedValues.QUEUED.value) -> str:
        job_id = str(uuid4())
        put_processingjobmodel_item(
            job_id=job_id,
            request_payload={"color": "purple", "value": 1},
            status=status,
        )
        return job_id

    def test_get_job_returns_as_dict_shape(self):
        job_id = self._seed_job()
        item = self.client.get_job(job_id)
        self.assertEqual(item["job_id"], job_id)
        self.assertEqual(item["status"], StatusSupportedValues.QUEUED.value)
        # as_dict() surfaces result_count and iso datetimes, never the raw sort_key
        self.assertIn("result_count", item)
        self.assertNotIn("sort_key", item)

    def test_get_job_missing_raises(self):
        with pytest.raises(JobNotFoundError):
            self.client.get_job(str(uuid4()))

    def test_submit_result_sets_completed(self):
        job_id = self._seed_job()
        item = self.client.submit_result(job_id, {"result": 7})
        self.assertEqual(item["status"], StatusSupportedValues.COMPLETED.value)
        self.assertEqual(item["response_payload"], {"result": 7})
        self.assertIsNotNone(item["completed_datetime"])

    def test_submit_result_invalid_payload_raises(self):
        job_id = self._seed_job()
        # TestResponsePostPayloadModel requires an int `result`
        with pytest.raises(ValidationError):
            self.client.submit_result(job_id, {"result": "not-an-int"})
        # job is untouched by a rejected submission
        self.assertEqual(self.client.get_job(job_id)["status"], StatusSupportedValues.QUEUED.value)

    def test_submit_result_missing_job_raises(self):
        with pytest.raises(JobNotFoundError):
            self.client.submit_result(str(uuid4()), {"result": 1})

    def test_submit_result_without_response_model_raises(self):
        client = JobsClient()
        job_id = self._seed_job()
        with pytest.raises(ValueError, match="response_model"):
            client.submit_result(job_id, {"result": 1})

    def test_mark_error_appends_list(self):
        job_id = self._seed_job(status=StatusSupportedValues.PROCESSING.value)
        item = self.client.mark_error(job_id, ["boom"])
        self.assertEqual(item["status"], StatusSupportedValues.ERROR.value)
        self.assertEqual(item["errors"], ["boom"])
        # a second failure appends rather than overwrites
        item = self.client.mark_error(job_id, ["again"])
        self.assertEqual(item["errors"], ["boom", "again"])

    def test_mark_error_accepts_string(self):
        job_id = self._seed_job()
        item = self.client.mark_error(job_id, "single")
        self.assertEqual(item["errors"], ["single"])

    def test_set_status_transition(self):
        job_id = self._seed_job()
        item = self.client.set_status(job_id, StatusSupportedValues.PROCESSING.value)
        self.assertEqual(item["status"], StatusSupportedValues.PROCESSING.value)

    def test_set_status_invalid_raises(self):
        job_id = self._seed_job()
        with pytest.raises(ValueError, match="invalid status"):
            self.client.set_status(job_id, "not-a-status")

    def test_submit_result_parity_with_put_route(self):
        """The client and the PUT route must produce the same terminal state."""
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
        app.config["TESTING"] = True
        http = app.test_client()

        # one job completed via the HTTP route, one via the client
        route_job = self._seed_job()
        client_job = self._seed_job()

        route_resp = http.put(f"/jobs/{route_job}", json={"result": 42})
        self.assertEqual(route_resp.status_code, HTTPStatus.OK)
        route_item = route_resp.json

        client_item = self.client.submit_result(client_job, {"result": 42})

        # compare everything that is not job-identity or wall-clock
        ignore = {"job_id", "registered_datetime", "updated_datetime", "completed_datetime"}
        route_cmp = {k: v for k, v in route_item.items() if k not in ignore}
        client_cmp = {k: v for k, v in client_item.items() if k not in ignore}
        self.assertEqual(route_cmp, client_cmp)
