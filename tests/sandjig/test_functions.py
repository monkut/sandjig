import datetime
from http import HTTPStatus
from unittest import TestCase

import pytest

from sandjig import create_app

from ..utils import TestRequestPostPayloadModel, TestResponsePostPayloadModel, reset_dynamodb, reset_sqs_queue


class FunctionsTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()

    def test_build_app_api(self):
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
        app.config["TESTING"] = True
        client = app.test_client()

        valid_request_data = {"color": "purple", "value": 1}
        url = "/jobs"
        response = client.post(url, json=valid_request_data)
        self.assertEqual(response.status_code, HTTPStatus.CREATED, response.data)

        assert "job_id" in response.json
        job_id = response.json["job_id"]
        self.assertDictEqual(response.json["request_payload"], valid_request_data)

        url = f"/jobs/{job_id}"
        response = client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_build_app_with_endpoint_prefix(self):
        endpoint_prefix = "/testprefix"
        config = {
            "ENDPOINT_PREFIX": endpoint_prefix,
        }
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=config)
        app.config["TESTING"] = True
        client = app.test_client()

        valid_request_data = {"color": "purple", "value": 1}
        url = f"{endpoint_prefix}/jobs"
        response = client.post(url, json=valid_request_data)
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

        assert "job_id" in response.json
        job_id = response.json["job_id"]
        self.assertDictEqual(response.json["request_payload"], valid_request_data)

        url = f"{endpoint_prefix}/jobs/{job_id}"
        response = client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_build_app_with_endpoint_prefix__invalid(self):
        endpoint_prefix = "missingforwardslashprefix"
        config = {
            "ENDPOINT_PREFIX": endpoint_prefix,
        }
        with pytest.raises(AssertionError):
            # The expected '/' is missing from the assigned ENDPOINT_PREFIX value
            create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=config)

    def test_responsepostbodylistmodel_model_dump(self):
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)

        job = app.JobResponseModel(
            job_id="f02e2c3a-8de1-49af-9d0d-6220c0021999",
            registered_datetime="2023-10-01T12:00:00Z",
            updated_datetime=datetime.datetime(2023, 10, 1, 12, 0, 0, tzinfo=datetime.UTC),
            completed_datetime=None,
            status="completed",
            errors=None,
            result_count=1,
            settings=None,
            request_payload=TestRequestPostPayloadModel(color="purple", value=1),
            response_payload=TestResponsePostPayloadModel(result=0),
        )
        self.assertTrue(job)
        self.assertTrue(job.model_dump())
        self.assertTrue(job.model_dump_json())
        responsepostbodylist = app.ResponsePostBodyListModel(
            Jobs=[job],
        )
        self.assertTrue(responsepostbodylist)
        self.assertTrue(responsepostbodylist.model_dump())
        self.assertTrue(responsepostbodylist.model_dump_json())
