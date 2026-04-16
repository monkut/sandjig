import datetime
import json
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from unittest import TestCase
from urllib.parse import quote_plus

import pytest
from flask import Response

from sandjig import create_app, settings
from sandjig.aws import SQS_CLIENT
from sandjig.jobsapi.dyanmodb.models import ProcessingSettingsModel
from sandjig.jobsapi.validation.definitions import StatusSupportedValues, ValidPatchValues

from ...utils import (
    BadSettingsModel,
    SettingsModel,
    TestRequestPostPayloadModel,
    TestResponsePostPayloadModel,
    get_authorization_basic_auth,
    reset_dynamodb,
    reset_sqs_queue,
)


class JobsApiAppTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()

        self.app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _create_job_request(self, payload: dict | None = None) -> Response:
        if not payload:
            payload = {"color": "purple", "value": 1}
        url = "/jobs"
        response = self.client.post(url, json=payload)
        return response

    def test_create_app__with_callback_function_valid(self):
        with TemporaryDirectory(prefix="test") as d:
            target_directory = Path(d)
            filename = "testfile.txt"
            sample_filepath = target_directory / filename

            def dummy_func(job_id: str, job_definition: dict) -> None:  # noqa: ARG001
                sample_filepath.write_text("test", encoding="utf8")

            config = {"JOBREQUEST_CALLBACK_FUNCTION": dummy_func}
            app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=config)
            app.config["TESTING"] = True
            self.client = app.test_client()
            valid_request_data = {"color": "purple", "value": 1}
            url = "/jobs"
            response = self.client.post(url, json=valid_request_data)
            self.assertEqual(response.status_code, HTTPStatus.CREATED)
            self.assertTrue(sample_filepath.exists())

    def test_create_app__with_callback_function_invalid(self):
        noncallable = "dummy"
        config = {"JOBREQUEST_CALLBACK_FUNCTION": noncallable}
        try:
            create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=config)
            self.fail("AssertionError not raised")
        except AssertionError:
            pass

    def test_api_post__invalid_missing_required(self):
        valid_request_data = {"value": 1}
        url = "/jobs"
        response = self.client.post(url, json=valid_request_data)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

    def test_api_post__valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        url = "/jobs"
        response = self.client.post(url, json=valid_request_data)
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertDictEqual(response.json["request_payload"], valid_request_data)

        # JobResponseModel
        expected_keys = (
            "job_id",
            "registered_datetime",
            "updated_datetime",
            "completed_datetime",
            "status",
            "result_count",
            "request_payload",
            "response_payload",
        )
        for expected_key in expected_keys:
            self.assertIn(expected_key, response.json)

        # check job in sqs queue
        response = SQS_CLIENT.receive_message(
            QueueUrl=settings.PROCESSINGJOB_REQUEST_QUEUE_URL, AttributeNames=["All'"], MaxNumberOfMessages=10
        )
        if "Messages" not in response:
            self.fail(f"No Messages in Queue: {settings.PROCESSINGJOB_REQUEST_QUEUE_URL}")
        messages = response["Messages"]
        self.assertEqual(len(messages), 1)
        message = messages[0]
        body = json.loads(message["Body"])
        self.assertDictEqual(body["request_payload"], valid_request_data, body["request_payload"])
        self.assertIn("settings", body)
        # when settings is *undefined* on app creation, settiongs will be None
        expected = {}
        self.assertDictEqual(body["settings"], expected)

    def test_api_jobid_get__valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        assert "job_id" in response.json
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.json["status"], StatusSupportedValues.QUEUED)
        self.assertDictEqual(response.json["request_payload"], valid_request_data)

    def test_api_jobid_get__paged__empty(self):
        url = "/jobs"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_api_jobid_get__paged__valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        assert "job_id" in response.json

        url = "/jobs"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_api_jobid_get__paged_expose_headers_valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        assert "job_id" in response.json

        url = "/jobs"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIn("Access-Control-Expose-Headers", response.headers)
        expected = "X-Next"
        self.assertEqual(response.headers["Access-Control-Expose-Headers"], expected)

    def test_api_jobid_post__valid__with_basicauth(self):
        username = "testuser"
        password = "test1234"  # noqa: S105
        config = {"BASIC_AUTH_FORCE": True, "BASIC_AUTH_USERNAME": username, "BASIC_AUTH_PASSWORD": password}
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=config)
        app.config["TESTING"] = True
        self.client = app.test_client()

        basic_auth_str = get_authorization_basic_auth(username=username, password=password)
        headers = {"Authorization": basic_auth_str}
        valid_request_data = {"color": "purple", "value": 1}
        url = "/jobs"
        response = self.client.post(url, json=valid_request_data, headers=headers)
        self.assertEqual(response.status_code, HTTPStatus.CREATED)

    def test_api_jobid_patch__invalid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        content = {"status": "other"}
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

    def test_api_jobid_patch__valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        content = {"status": ValidPatchValues.CANCELLED.value}
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED, response.data)

        valid_request_data = {"color": "red", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        content = {"status": ValidPatchValues.ERROR.value}
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED)

    def test_api_jobid_patch__valid_with_errors(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        content = {"status": ValidPatchValues.CANCELLED.value}
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED, response.data)

        valid_request_data = {"color": "red", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        url = f"/jobs/{job_id}"
        errors = ["error1", "error2"]
        content = {"status": ValidPatchValues.ERROR.value, "errors": errors}
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED)
        job = response.json
        self.assertListEqual(job["errors"], errors)

    def test_api_jobid_put__valid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        # post response
        # - See:
        #   tests.utils.TestResponsePostPayloadModel
        response_data = {"result": 99}
        url = f"/jobs/{job_id}"
        response = self.client.put(url, json=response_data)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data.decode("utf8"))

    def test_api_jobid_put__invalid(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_id = response.json["job_id"]

        # post response
        # - See:
        #   tests.utils.TestResponsePostPayloadModel
        response_data = {"othervaluekey": 99}
        url = f"/jobs/{job_id}"
        response = self.client.put(url, json=response_data)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

        response_data = {"result": "invalidvalue"}
        response = self.client.put(url, json=response_data)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY)

    def test_api_healthcheck_options(self):
        url = "/healthcheck"
        response = self.client.options(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_api_response_cors_headers(self):
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        expected_headers = ("Access-Control-Allow-Origin", "Access-Control-Allow-Methods")
        for expected in expected_headers:
            self.assertIn(expected, response.headers)

    def test_api_jobs_processingjoblist_get__registered_datetime_invalid(self):
        valid_request_data = {"color": "purple", "value": 1}
        self._create_job_request(payload=valid_request_data)

        valid_request_data = {"color": "red", "value": 1}
        self._create_job_request(payload=valid_request_data)

        # test registered_datetime conditions INVALID
        invalid_query_params = [
            ("registered_datetime_gte", "invalid-datetime"),
            ("registered_datetime_lte", "invalid-datetime"),
        ]
        for query_param, invalid_value in invalid_query_params:
            url = f"/jobs?{query_param}={invalid_value}"
            response = self.client.get(url)
            self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY, response.data.decode("utf8"))

    def test_api_jobs_processingjoblist_get_registered_datetime_gte_condition(self):
        valid_request_data = {"color": "purple", "value": 1}
        self._create_job_request(payload=valid_request_data)

        sleep(1)  # ensure job_2 timestamp is different/after job_1

        response = self._create_job_request(payload=valid_request_data)
        job_2_id = response.json["job_id"]
        job_2_registered_datetime = response.json["registered_datetime"]

        response = self.client.get("/jobs")
        assert response.status_code == HTTPStatus.OK
        expected_job_count = 2
        assert len(response.json["Jobs"]) == expected_job_count, response.json["Jobs"]

        encoded_job_2_registered_datetime = quote_plus(job_2_registered_datetime)
        url = f"/jobs?registered_datetime_gte={encoded_job_2_registered_datetime}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data.decode("utf8"))

        # check that job_2 ONLY is returned
        expected_result_job_count = 1
        self.assertEqual(len(response.json["Jobs"]), expected_result_job_count)
        expected_job_id = job_2_id
        job = response.json["Jobs"][0]
        self.assertEqual(job["job_id"], expected_job_id, job)

    def test_api_jobs_processingjoblist_get_registered_datetime_lte_condition(self):
        one_second = 1
        valid_request_data = {"color": "purple", "value": 1}
        response = self._create_job_request(payload=valid_request_data)
        job_1_id = response.json["job_id"]
        job_1_registered_datetime = response.json["registered_datetime"]

        sleep(one_second)  # ensure job_2 timestamp is different/after job_1

        self._create_job_request(payload=valid_request_data)

        response = self.client.get("/jobs")
        assert response.status_code == HTTPStatus.OK
        expected_job_count = 2
        assert len(response.json["Jobs"]) == expected_job_count, response.json["Jobs"]

        encoded_job_1_registered_datetime = quote_plus(job_1_registered_datetime)
        url = f"/jobs?registered_datetime_lte={encoded_job_1_registered_datetime}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data.decode("utf8"))

        # check that job_1 ONLY is returned
        expected_result_job_count = 1
        self.assertEqual(len(response.json["Jobs"]), expected_result_job_count)
        expected_job_id = job_1_id
        job = response.json["Jobs"][0]
        self.assertEqual(job["job_id"], expected_job_id, job)

    def test_api_jobs_processingjoblist_get_registered_datetime_conditions_combined(self):
        one_second = 1
        valid_request_data = {"color": "purple", "value": 1}
        self._create_job_request(payload=valid_request_data)

        sleep(one_second)  # ensure job_2 timestamp is different/after job_1

        response = self._create_job_request(payload=valid_request_data)
        job_2_id = response.json["job_id"]
        job_2_registered_datetime = response.json["registered_datetime"]

        sleep(one_second)  # ensure job_2 timestamp is different/after job_2

        response = self._create_job_request(payload=valid_request_data)
        job_3_id = response.json["job_id"]

        sleep(one_second)  # ensure job_2 timestamp is different/after job_3

        response = self._create_job_request(payload=valid_request_data)
        job_4_id = response.json["job_id"]
        job_4_registered_datetime = response.json["registered_datetime"]

        sleep(one_second)  # ensure job_2 timestamp is different/after job_4
        self._create_job_request(payload=valid_request_data)

        response = self.client.get("/jobs")
        assert response.status_code == HTTPStatus.OK
        expected_job_count = 5
        assert len(response.json["Jobs"]) == expected_job_count, response.json["Jobs"]

        gte_filter_datetime = job_2_registered_datetime
        lte_filter_datetime = job_4_registered_datetime

        encoded_gte_filter_datetime = quote_plus(gte_filter_datetime)
        encoded_lte_filter_datetime = quote_plus(lte_filter_datetime)
        url = (
            f"/jobs?registered_datetime_lte={encoded_lte_filter_datetime}"
            f"&registered_datetime_gte={encoded_gte_filter_datetime}"
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data.decode("utf8"))

        # check that job_1 ONLY is returned
        expected_result_job_count = 3
        self.assertEqual(len(response.json["Jobs"]), expected_result_job_count)
        actual_job_ids = {job["job_id"] for job in response.json["Jobs"]}
        expected_job_ids = (job_2_id, job_3_id, job_4_id)
        for expected_job_id in expected_job_ids:
            self.assertIn(expected_job_id, actual_job_ids)


EIGHT_ITEMS_PER_PAGE = 8


class JobsApiAppAdjustablePagingTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()

    def _create_job_request(self, payload: dict | None = None) -> Response:
        if not payload:
            payload = {"color": "purple", "value": 1}
        url = "/jobs"
        response = self.client.post(url, json=payload)
        return response

    @pytest.mark.skipif(
        settings.MIN_ITEMS_PER_PAGE != EIGHT_ITEMS_PER_PAGE,
        reason="Must apply MIN_ITEMS_PER_PAGE=8 envar before running!",
    )
    def test_paging_adjustable(self):
        self.app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        url = "/jobs?limit=8"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)


class SettingsApiAppTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()

    def _prepare_app(self, with_settings: bool = False) -> None:
        if with_settings:
            app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, SettingsModel)
        else:
            app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_settingsmodel_validation(self):
        data = {
            "intvalue": 99,
            "strvalue": "other",
            "datetimevalue": datetime.datetime.now(),
            "boolvalue": True,
        }
        SettingsModel(**data)

    def test_settingsmodel_definition_error(self):
        with pytest.raises(ValueError):
            create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, BadSettingsModel)

    def test_settings_defined(self):
        self._prepare_app(with_settings=True)

        url = "/settings"
        # OPTIONSで200 OK が返してくることを確認
        response = self.client.options(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_settings_patch__valid(self):
        self._prepare_app(with_settings=True)
        url = "/settings"

        updated_datetime_value = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC).isoformat()
        # values defined in SettingsModel
        content = {
            "intvalue": 99,
            "strvalue": "other",
            "datetimevalue": updated_datetime_value,
            "boolvalue": True,
        }
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED, response.data)
        response_data = response.json
        expected_keys = ("updated_datetime",)
        for expected in expected_keys:
            self.assertIn(expected, response_data)

        # remove unnecessary keys
        response_data.pop("updated_datetime")
        for k, v in response_data.items():
            self.assertEqual(v, content[k])

    def test_settings_patch__valid_partial(self):
        self._prepare_app(with_settings=True)
        url = "/settings"

        updated_datetime_value = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC).isoformat()
        # values defined in SettingsModel
        content = {
            "intvalue": 99,
            "strvalue": "other",
            "datetimevalue": updated_datetime_value,
        }
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.ACCEPTED, response.data)
        response_data = response.json
        expected_keys = ("updated_datetime",)
        for expected in expected_keys:
            self.assertIn(expected, response_data)

        # remove unnecessary keys
        response_data.pop("updated_datetime")
        for k, v in content.items():
            self.assertEqual(v, v)
        self.assertEqual(response_data["boolvalue"], False)  # existing value

    def test_settings_patch__invalid__parameterdoesnotexist(self):
        self._prepare_app(with_settings=True)
        url = "/settings"

        updated_datetime_value = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC).isoformat()
        # values defined in SettingsModel
        content = {
            "intvalue": 99,
            "strvalue": "other",
            "datetimevalue": updated_datetime_value,
            "nonexistent": True,
        }
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY, response.data)

    def test_settings_patch__invalid__parameterinvalidtype(self):
        self._prepare_app(with_settings=True)
        url = "/settings"

        updated_datetime_value = datetime.datetime(1970, 1, 1, tzinfo=datetime.UTC).isoformat()
        # values defined in SettingsModel
        content = {
            "intvalue": "somestring",
            "strvalue": "other",
            "datetimevalue": updated_datetime_value,
            "boolvalue": True,
        }
        response = self.client.patch(url, json=content)
        self.assertEqual(response.status_code, HTTPStatus.UNPROCESSABLE_ENTITY, response.data)

    def test_settings_get(self):
        self._prepare_app(with_settings=True)
        url = "/settings"

        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data)

    def test_settings_get__itemdoesnotexist(self):
        self._prepare_app(with_settings=True)

        # delete settings
        item = ProcessingSettingsModel.get_processingsettingsmodel_item(settings.SETTINGS_ID, as_dict=False)
        item.delete()

        # confirm new entry is created/added
        url = "/settings"
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK, response.data)

    def test_api_jobid_post__with_settings(self):
        self._prepare_app(with_settings=True)

        valid_request_data = {"color": "purple", "value": 1}
        url = "/jobs"
        response = self.client.post(url, json=valid_request_data)
        self.assertEqual(response.status_code, HTTPStatus.CREATED)
        self.assertDictEqual(response.json["request_payload"], valid_request_data)
        self.assertIn("settings", response.json)

        expected = SettingsModel().model_dump()
        self.assertDictEqual(response.json["settings"], expected)

    def test_api_openapi_html(self):
        self._prepare_app()
        url = f"{settings.OPENAPI_SCHEMA_PATH}"
        # OPTIONSで200 OK が返してくることを確認
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTrue(response.data)


class BuiltAppBasicAuthTestCase(TestCase):
    def setUp(self) -> None:
        reset_dynamodb()
        reset_sqs_queue()
        basicauth_config = {
            "BASIC_AUTH_FORCE": True,
            "BASIC_AUTH_USERNAME": "basicauthuser",
            "BASIC_AUTH_PASSWORD": "supersecretpassword",
        }
        app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config=basicauth_config)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_api_healthcheck_options(self):
        url = "/healthcheck"
        # GETで401 UNAUTHORIZED が返してくることを確認
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED)

        # OPTIONSで200 OK が返してくることを確認
        response = self.client.options(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)

    def test_api_jobs_get__with_cors_headers__valid_auth(self):
        url = "/jobs"
        # GETで401 UNAUTHORIZED が返してくることを確認
        response = self.client.get(url, headers={"www_authenticate": "invalid"})
        self.assertEqual(response.status_code, HTTPStatus.UNAUTHORIZED, response.data.decode("utf8"))

        expected_headers = ("Access-Control-Allow-Origin", "Access-Control-Allow-Methods")
        for expected in expected_headers:
            self.assertIn(expected, response.headers)

    def test_api_jobs_options__with_cors_headers__invalid_auth(self):
        url = "/jobs"
        # OPTIONSで200 OK が返してくることを確認
        response = self.client.options(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        expected_headers = (
            "Access-Control-Allow-Origin",
            "Access-Control-Allow-Methods",
            "Access-Control-Allow-Headers",
        )
        for expected in expected_headers:
            self.assertIn(expected, response.headers)
        actual = response.headers.get("Access-Control-Allow-Headers")
        allowed_headers = ("Origin", "Content-Type", "Accept", "Authorization")
        expected = ",".join(allowed_headers)
        self.assertEqual(actual, expected)

    def test_api_openapi_schema(self):
        url = f"{settings.OPENAPI_SCHEMA_PATH}/openapi.json"
        # OPTIONSで200 OK が返してくることを確認
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
