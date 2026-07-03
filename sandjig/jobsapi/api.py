"""Processing Jobs Request API."""

import datetime
import json
import logging
import os
import sys
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable
from http import HTTPStatus
from pathlib import Path
from string import Template
from typing import Any
from uuid import UUID, uuid4

from flask import Flask, request
from flask.wrappers import Request, Response
from flask_basicauth import BasicAuth
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator
from pynamodb.exceptions import QueryError, VerboseClientError

from .. import settings
from ..aws import SQS_CLIENT
from ..functions import check_model_defaults, create_dynamodb_resources, get_timestamp_now, mask_string, noauthdecorator
from ..models import RequestPostPayloadBaseModel, ResponsePostPayloadBaseModel, SettingsBaseModel
from .dynamodb.models import ItemDoesNotExistError, ProcessingJobModel, ProcessingSettingsModel
from .exceptions import QueryParamError
from .functions import CustomJSONProvider, get_datettime_range_args, strtobool
from .validation.definitions import (
    BadRequest400Response,
    JobsApiQueryParams,
    NotFound404Response,
    ProcessingJobPatchBody,
    StatusSupportedValues,
    TooManyRequests429Response,
)

logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    force=True,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(funcName)s: %(message)s",
)
logger = logging.getLogger(__name__)  # pylint: disable=C0103

# reduce logging output from noisy packages
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("pynamodb.connection.base").setLevel(logging.WARNING)
if not settings.ENABLE_BOTO3_LOGGING:
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)


FIFTEEN_MINUTES = 60 * 15
STAGE = os.getenv("STAGE", "")
JST = datetime.timezone(datetime.timedelta(hours=+9), "JST")


STATIC_ASSETS_DIRECTORY = Path(__file__).parent.parent / "static"
assert STATIC_ASSETS_DIRECTORY.exists(), STATIC_ASSETS_DIRECTORY
REQUIRED_ENDPOINT_PREFIX_CHARACTER = "/"


def _get_validated_hook(config: dict[str, Any], config_key: str) -> Callable | None:
    """Retrieve an optional hook callable from config, asserting it is callable when set."""
    hook = config.get(config_key)
    assert hook is None or isinstance(hook, Callable), f"given config['{config_key}'] is not Callable!"
    return hook


def create_app(  # noqa: C901, PLR0915
    RequestPostBodyModel: type[RequestPostPayloadBaseModel],  # noqa: C901, N806, N803
    ResponsePostBodyModel: type[ResponsePostPayloadBaseModel],  # noqa: C901, N806, N803
    SettingsModel: type[SettingsBaseModel] = None,  # noqa: C901, N806, N803
    config: dict[str, Any] | None = None,
) -> Flask:  # noqa: C901
    """Build the flask application to interface with."""
    from flask_pydantic_spec import FlaskPydanticSpec, Request as SpecRequest, Response as SpecResponse

    if config is None:
        config = {}
    assert issubclass(RequestPostBodyModel, RequestPostPayloadBaseModel)
    assert issubclass(ResponsePostBodyModel, ResponsePostPayloadBaseModel)
    if SettingsModel:
        assert issubclass(SettingsModel, SettingsBaseModel)

    api_title = config.get("API_TITLE", settings.API_TITLE)
    api_version = config.get("API_VERSION", settings.API_VERSION)
    json_as_ascii = config.get("JSON_AS_ASCII", settings.JSON_AS_ASCII)
    request_sqs_url = config.get("SQS_QUEUE_URL", settings.PROCESSINGJOB_REQUEST_QUEUE_URL)
    if request_sqs_url.replace('"', "").strip() in ("None", "False", "false", "", None):
        request_sqs_url = None

    # Use create_model: closure-scoped `class` + pydantic v2 can't resolve
    # RequestPostBodyModel / ResponsePostBodyModel / SettingsModel forward refs.
    class _JobResponseBase(BaseModel):
        def model_dump(self, *args, **kwargs) -> dict:
            d = super().model_dump(*args, **kwargs)
            for k, v in d.items():
                if v and k.endswith("datetime"):
                    d[k] = v.isoformat()
            return d

    # `SettingsModel | None` would raise TypeError when SettingsModel is None.
    settings_field: tuple[Any, Any] = (SettingsModel | None, None) if SettingsModel is not None else (None, None)

    JobResponse = create_model(  # noqa: N806
        "JobResponse",
        __base__=_JobResponseBase,
        job_id=(UUID, ...),
        registered_datetime=(datetime.datetime, ...),
        updated_datetime=(datetime.datetime, ...),
        completed_datetime=(datetime.datetime | None, None),
        status=(StatusSupportedValues, ...),
        errors=(list[str] | None, None),
        result_count=(int, Field(description="出力される結果の数", default=0)),
        settings=settings_field,
        request_payload=(RequestPostBodyModel, ...),
        response_payload=(ResponsePostBodyModel | None, None),
    )

    class ResponsePostBodyListModel(BaseModel):
        """Create Validation model for List of Responses"""

        Jobs: list[JobResponse]

    app = Flask(__name__)  # pylint: disable=invalid-name
    app.config["TESTING"] = strtobool(os.getenv("FLASK_APP_TESTING", "False"))
    app.json = CustomJSONProvider(app)
    app.json.ensure_ascii = json_as_ascii
    app.json.compact = False
    app.JobResponseModel = JobResponse
    app.ResponsePostBodyListModel = ResponsePostBodyListModel
    api = FlaskPydanticSpec("jobsapi", title=api_title, version=api_version, path=settings.OPENAPI_SCHEMA_PATH)
    endpoint_prefix = config.get("ENDPOINT_PREFIX", "")
    if endpoint_prefix and not app.config["TESTING"]:
        assert endpoint_prefix.startswith(REQUIRED_ENDPOINT_PREFIX_CHARACTER), (
            f"REQUIRED_ENDPOINT_PREFIX_CHARACTER missing, '{REQUIRED_ENDPOINT_PREFIX_CHARACTER}'!"
        )
    authorizationdecorator = noauthdecorator
    Headers = None  # noqa: N806
    if config:
        if "BASIC_AUTH_FORCE" in config and config["BASIC_AUTH_FORCE"] is True:
            if "BASIC_AUTH_USERNAME" in config and "BASIC_AUTH_PASSWORD" in config:
                app.config["BASIC_AUTH_USERNAME"] = config["BASIC_AUTH_USERNAME"]
                app.config["BASIC_AUTH_PASSWORD"] = config["BASIC_AUTH_PASSWORD"]
            else:
                logger.info("Loading BASIC_AUTH_USERNAME, BASIC_AUTH_PASSWORD from ENVAR...")
                app.config["BASIC_AUTH_USERNAME"] = os.getenv(
                    "BASIC_AUTH_USERNAME", settings.DEFAULT_BASIC_AUTH_USERNAME
                )
                app.config["BASIC_AUTH_PASSWORD"] = os.getenv("BASIC_AUTH_PASSWORD", None)
                if not app.config["BASIC_AUTH_PASSWORD"]:
                    raise ValueError("Required environment variable not set: BASIC_AUTH_PASSWORD")
                logger.info("Loading BASIC_AUTH_USERNAME, BASIC_AUTH_PASSWORD from ENVAR...SUCCESS")
            logger.info(f" -- BASIC_AUTH_USERNAME={app.config['BASIC_AUTH_USERNAME']}")
            masked_password = mask_string(app.config["BASIC_AUTH_PASSWORD"])
            logger.info(f" -- BASIC_AUTH_PASSWORD={masked_password}")
            basicauth = BasicAuth(app)
            authorizationdecorator = basicauth.required

            class Headers(BaseModel):
                model_config = ConfigDict(
                    extra="allow",
                )

                authorization: str

                @model_validator(mode="before")
                def lower_keys(cls, values: dict) -> dict:  # noqa: N805
                    return {key.lower(): value for key, value in values.items()}

        # validate function is callable
        callback_function = config.get("JOBREQUEST_CALLBACK_FUNCTION")
        if callback_function:
            assert isinstance(callback_function, Callable), (
                "given config['JOBREQUEST_CALLBACK_FUNCTION'] is not Callable!"
            )

    else:
        callback_function = None

    # optional request-processing hooks (validated as callable when set)
    authorization_function = _get_validated_hook(config, "JOBREQUEST_AUTHORIZATION_FUNCTION")
    transform_function = _get_validated_hook(config, "JOBREQUEST_TRANSFORM_FUNCTION")

    # add interface for obtaining the current authorization decorator
    app.get_authorization_decorator = lambda *_: authorizationdecorator

    def fps_error_logger(req: Request, resp: Response, err: str, _: Any = None) -> None:  # noqa: ANN401
        """Error logger for when flask_pydantic_spec Request/Response Validation fails"""
        if err:
            logger.debug("flask_pydantic_spec request/response validation error:")
            logger.debug(f"req={req}")
            logger.debug(f"resp={resp}")
            logger.error(err)

    def execute_callback(job_id: str, job_definition: dict) -> str | None:
        function_name = callback_function.__name__
        logger.info(
            f"calling user defined config['JOBREQUEST_CALLBACK_FUNCTION'] ({function_name}), job_id={job_id} ..."
        )
        error = None
        try:
            callback_function(job_id=job_id, job_definition=job_definition)
            logger.info(
                f"calling user defined config['JOBREQUEST_CALLBACK_FUNCTION'] "
                f"({function_name}), job_id={job_id} ... DONE"
            )
        except Exception as e:
            error = f"ERROR - {str(e.args)}"
            logger.exception(error)

        return error

    def get_latest_settings() -> dict:
        result = {}
        if SettingsModel is not None:
            from .dynamodb.models import ItemDoesNotExistError

            try:
                item: dict = ProcessingSettingsModel.get_processingsettingsmodel_item(
                    settings_id=settings.SETTINGS_ID, as_dict=True
                )
                result = item
            except ItemDoesNotExistError:
                logger.exception("ItemDoesNotExist: SettingsModel/ProcessingSettingsModel Entry!")
                logger.info("Creating initial SettingsModel/ProcessingSettingsModel Entry...")
                settings_defaults = SettingsModel()
                item: ProcessingSettingsModel = ProcessingSettingsModel(
                    settings_id=settings.SETTINGS_ID,
                    updated_timestamp=get_timestamp_now(),
                    settings=settings_defaults.model_dump(),
                )
                item.save()
                logger.info("Creating initial SettingsModel/ProcessingSettingsModel Entry... DONE")
                item: dict = ProcessingSettingsModel.get_processingsettingsmodel_item(
                    settings_id=settings.SETTINGS_ID, as_dict=True
                )
                result = item
        return result

    @app.after_request
    def add_cors_headers(response: Response) -> Response:
        """
        Add CORS headers for VUEJS/AXIOS requests,
        where OPTIONS *pre-flight* request is made prior to the actual desired request
        """
        response.headers.set("Access-Control-Allow-Origin", "*")
        response.headers.set("Access-Control-Allow-Methods", "*")
        allowed_headers = ("Origin", "Content-Type", "Accept", "Authorization")
        response.headers.set("Access-Control-Allow-Headers", ",".join(allowed_headers))
        if request.method == "OPTIONS":
            response.status_code = HTTPStatus.OK
        return response

    # Defer AWS access out of create_app so app construction performs no network calls
    # (table creation runs once, on the first request handled by this process)
    dynamodb_resources_initialized = False

    @app.before_request
    def ensure_dynamodb_resources() -> None:
        nonlocal dynamodb_resources_initialized
        if not dynamodb_resources_initialized:
            create_dynamodb_resources(SettingsModel)
            dynamodb_resources_initialized = True

    @app.route(f"{endpoint_prefix}/jobs/<job_id>", methods=["GET"])
    @authorizationdecorator
    @api.validate(
        headers=Headers,
        resp=SpecResponse(HTTP_200=JobResponse, HTTP_401=None),
        tags=["jobs"],
        before=fps_error_logger,
        after=fps_error_logger,
    )
    def processingjob_get(job_id: str) -> tuple[dict, int]:
        """処理要求ジョブ状況のリストで返す（registered_datetimeの新しい日付時間→古い日付時間順で返される）"""
        try:
            logger.info(f"retrieving ProcessingJobModel(job_id={job_id})...")
            item: dict = ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=True)
        except ItemDoesNotExistError as e:
            logger.exception(f"job_id({job_id}) not found: {e.args}")
            return {"message": f"job_id({job_id}) not found"}, HTTPStatus.NOT_FOUND.value
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        logger.debug(f"item={item}")

        return item, HTTPStatus.OK.value

    @app.route(f"{endpoint_prefix}/jobs/<job_id>", methods=["PUT"])
    @authorizationdecorator
    @api.validate(
        headers=Headers,
        body=SpecRequest(ResponsePostBodyModel),
        resp=SpecResponse(
            HTTP_200=JobResponse,
            HTTP_400=BadRequest400Response,
            HTTP_401=None,
            HTTP_404=NotFound404Response,
            HTTP_429=TooManyRequests429Response,
        ),
        tags=["jobs"],
        before=fps_error_logger,
        after=fps_error_logger,
    )
    def processingjob_put(job_id: str) -> tuple[dict, int]:
        """ジョブ(job_id)の結果を提出するメッソド"""
        logger.debug("request.data=%s", request.data)
        # apigateway may send requests as base64
        # -> Handle both json and base64 as body types
        try:
            logger.debug("decoding body as base64...")
            json_body = b64decode(request.data)
            request_data = json.loads(json_body)
            logger.debug("SUCCESS!")
        except ValueError:
            logger.debug("FAILED!")
            logger.debug("decoding body as json...")
            request_data = request.get_json(force=True)
            logger.debug("SUCCESS!")
        try:
            validated_data = ResponsePostBodyModel(**request_data)
        except ValidationError as e:
            return {
                "message": f"ERROR - invalid input for request: {e.args}"
            }, HTTPStatus.UNPROCESSABLE_ENTITY.value  # 422

        try:
            logger.info(f"retrieving ProcessingJobModel(job_id={job_id})...")
            item: ProcessingJobModel = ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=False)
        except ItemDoesNotExistError as e:
            logger.exception(f"job_id({job_id}) not found: {e.args}")
            return {"message": f"job_id({job_id}) not found"}, HTTPStatus.NOT_FOUND.value
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        logger.info(f'updating ProcessingJobModel(job_id={job_id}, status="completed")...')
        current_timestamp_utc = get_timestamp_now()
        try:
            item.update(
                actions=[
                    ProcessingJobModel.status.set(StatusSupportedValues.COMPLETED.value),
                    ProcessingJobModel.updated_timestamp.set(current_timestamp_utc),
                    ProcessingJobModel.completed_timestamp.set(current_timestamp_utc),
                    ProcessingJobModel.response_payload.set(validated_data.model_dump()),
                ]
            )

            # retrieve the updated item
            item: dict = ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=True)
            logger.debug(f"item={item}")
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value
        return item, HTTPStatus.OK.value

    @app.route(f"{endpoint_prefix}/jobs/<job_id>", methods=["PATCH"])
    @authorizationdecorator
    @api.validate(
        headers=Headers,
        body=SpecRequest(ProcessingJobPatchBody),
        resp=SpecResponse(
            HTTP_200=JobResponse, HTTP_401=None, HTTP_404=NotFound404Response, HTTP_429=TooManyRequests429Response
        ),
        tags=["jobs"],
        before=fps_error_logger,
        after=fps_error_logger,
    )
    def processingjob_patch(job_id: str) -> tuple[dict, int]:
        """ジョブ(job_id)が未完了な状況を更新するメッソド（エラーを含む）"""
        logger.debug("request.data=%s", request.data)
        # apigateway may send requests as base64
        # -> Handle both json and base64 as body types
        try:
            logger.debug("decoding body as base64...")
            json_body = b64decode(request.data)
            request_data = json.loads(json_body)
            logger.debug("SUCCESS!")
        except ValueError:
            logger.debug("FAILED!")
            logger.debug("decoding body as json...")
            request_data = request.get_json(force=True)
            logger.debug("SUCCESS!")
        try:
            validated_data = ProcessingJobPatchBody(**request_data)
        except ValidationError as e:
            return {"message": f"ERROR - invalid input for request: {e.args}"}, HTTPStatus.UNPROCESSABLE_ENTITY.value

        try:
            logger.info(f"retrieving ProcessingJobModel(job_id={job_id})...")
            item: ProcessingJobModel = ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=False)
        except ItemDoesNotExistError as e:
            logger.exception(f"job_id({job_id}) not found: {e.args}")
            return {"message": f"job_id({job_id}) not found"}, HTTPStatus.NOT_FOUND.value
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        logger.info(f'updating ProcessingJobModel(job_id={job_id}, status="cancelled")...')
        current_timestamp_utc = get_timestamp_now()

        updated_errors = item.errors
        if not isinstance(updated_errors, list):
            updated_errors = []
        validated_new_errors = validated_data.errors
        if validated_new_errors is not None and isinstance(validated_new_errors, list):
            updated_errors.extend(validated_new_errors)
        elif validated_new_errors is not None and isinstance(validated_new_errors, str):
            updated_errors.append(validated_new_errors)

        try:
            item.update(
                actions=[
                    ProcessingJobModel.status.set(validated_data.status),
                    ProcessingJobModel.errors.set(updated_errors),
                    ProcessingJobModel.updated_timestamp.set(current_timestamp_utc),
                ]
            )
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        # retrieve the updated item
        item: dict = ProcessingJobModel.get_processingjobmodel_item(job_id=job_id, as_dict=True)
        logger.debug(f"item={item}")
        return item, HTTPStatus.ACCEPTED.value

    @app.route(f"{endpoint_prefix}/jobs", methods=["GET"])
    @authorizationdecorator
    @api.validate(
        query=JobsApiQueryParams,
        headers=Headers,
        resp=SpecResponse(HTTP_200=ResponsePostBodyListModel, HTTP_401=None, HTTP_429=TooManyRequests429Response),
        tags=["jobs"],
        before=fps_error_logger,
        after=fps_error_logger,
    )
    def processingjoblist_get() -> tuple[dict, int, dict[str, str]]:
        """GET method for ProcessingJobList"""
        logger.debug(f"request.args={request.args}")
        # paged results
        headers = {
            settings.NEXT_PAGE_HEADER: "",
            "Access-Control-Expose-Headers": settings.NEXT_PAGE_HEADER,
        }
        raw_jobids_filter = request.args.get("job_id", None)
        if raw_jobids_filter:
            jobids_filter = raw_jobids_filter.split(",")
            if len(jobids_filter) > settings.MAX_FILTER_JOBIDS:
                response_content = {
                    "message": (
                        f'ERROR - "job_id" filter count({len(jobids_filter)})'
                        f" > MAX_FILTER_JOBIDS({settings.MAX_FILTER_JOBIDS})!"
                    )
                }
                return response_content, HTTPStatus.UNPROCESSABLE_ENTITY.value, headers

            logger.debug("filter job_ids: %s", str(jobids_filter))
            try:
                # job_ids filter query is NOT paged!
                items: list[dict] = ProcessingJobModel.get_processingjobmodel_items(job_ids=jobids_filter, as_dict=True)
                all_jobs = {"Jobs": items}
            except (QueryError, VerboseClientError) as e:
                logger.exception(e.args)
                if not any(
                    error_message in str(e.args)
                    for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
                ):
                    raise  # re-raise QueryError Exception
                return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value, headers

            return all_jobs, HTTPStatus.OK.value, headers

        try:
            datetime_range_args = get_datettime_range_args(request)
        except QueryParamError as e:
            logger.exception("Unable to parse datetime range arguments")
            response_content = {
                "message": e.message,
            }
            return response_content, HTTPStatus.UNPROCESSABLE_ENTITY.value, headers

        limit = int(request.args.get("limit", settings.ITEMS_PER_PAGE))  # items_per_page
        status_filter = request.args.get("status", None)

        # check for last_evaluated_key
        last_ekey = request.args.get("ekey", None)
        logger.debug(f"last_ekey={last_ekey}")
        if last_ekey:
            # decode last_ekey
            last_ekey = json.loads(urlsafe_b64decode(last_ekey))
        try:
            paged_items, last_ekey = ProcessingJobModel.get_paged_results(
                last_evaluated_key=last_ekey, status_filter=status_filter, limit=limit, **datetime_range_args
            )
            logger.debug(f"returned len(paged_items)={len(paged_items)}, last_ekey={last_ekey}")
            if last_ekey:
                # encode last_ekey
                encoded_ekey = urlsafe_b64encode(json.dumps(last_ekey).encode("utf8")).decode("utf8")
                # set header: settings.NEXT_PAGE_HEADER -> link to next page
                headers[settings.NEXT_PAGE_HEADER] = f"/jobs?ekey={encoded_ekey}&limit={limit}"
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value, headers

        page_result = ResponsePostBodyListModel(Jobs=paged_items)
        page = page_result.model_dump()
        logger.debug(f"page_result.model_dump_json()={page}")
        return page, HTTPStatus.OK.value, headers

    def _processingjoblist_handle_callback(job_id: str, item: ProcessingJobModel) -> ProcessingJobModel:
        logger.info(
            f"callback_function ({callback_function.__name__}) defined calling execute_callback(job_id={job_id}) ..."
        )
        error = execute_callback(job_id=job_id, job_definition=item.as_dict())
        if error:
            logger.error(error)
            logger.error(
                f"callback_function ({callback_function.__name__}) "
                f"defined calling execute_callback(job_id={job_id}) ... ERROR"
            )

            logger.info(f"updating {job_id} to error status ...")
            if isinstance(item.errors, list):
                item.errors.append(error)
            else:
                item.errors = [error]

            item.status = StatusSupportedValues.ERROR.value
            item.save()
            logger.info(f"updating {job_id} to error status ... DONE")
        else:
            logger.info(
                f"callback_function ({callback_function.__name__}) "
                f"defined calling execute_callback(job_id=job_id) ... DONE"
            )
        return item  # return the item has it may have been updated with ERROR status

    def _create_processingjob(validated_data: RequestPostPayloadBaseModel) -> tuple[dict, int]:
        job_id = str(uuid4())
        current_settings = get_latest_settings()
        item = ProcessingJobModel(job_id=job_id, settings=current_settings, request_payload=validated_data.model_dump())
        try:
            item.save()
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        # get the sqs formatted message and send
        message_body_json_str = item.get_sqs_message()

        logger.debug(f"Queuing MessageBody: {message_body_json_str}")
        logger.debug(f"queue_url={request_sqs_url}")
        if request_sqs_url:
            logger.debug(f"sqs_endpoint={settings.AWS_SERVICE_ENDPOINTS['sqs']}")
            logger.info(f"queuing MessageBody: {message_body_json_str}")
            logger.debug(f"request_sqs_url={request_sqs_url}")
            SQS_CLIENT.send_message(QueueUrl=request_sqs_url, MessageBody=message_body_json_str)
            logger.info("Queuing MessageBody: SUCCESS!")

        # update state
        item.status = StatusSupportedValues.QUEUED.value
        item.updated_timestamp = get_timestamp_now()
        try:
            item.save()
        except (QueryError, VerboseClientError) as e:
            logger.exception(e.args)
            if not any(
                error_message in str(e.args)
                for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
            ):
                raise  # re-raise QueryError Exception
            return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

        logger.debug("callback_function: %s", str(callback_function))
        if callback_function:
            item = _processingjoblist_handle_callback(job_id, item)

        response_content = item.as_dict()

        status_code = HTTPStatus.CREATED.value
        return response_content, status_code

    @app.route(f"{endpoint_prefix}/jobs", methods=["POST"])
    @authorizationdecorator
    @api.validate(
        headers=Headers,
        body=SpecRequest(RequestPostBodyModel),
        resp=SpecResponse(
            HTTP_201=JobResponse, HTTP_400=BadRequest400Response, HTTP_401=None, HTTP_429=TooManyRequests429Response
        ),
        tags=["jobs"],
        before=fps_error_logger,
        after=fps_error_logger,
    )
    def processingjoblist_post() -> tuple[dict, int]:
        """新しいジョブ処理要求を依頼する"""
        logger.debug("request.data=%s", request.data)

        # apigateway may send requests as base64
        # -> Handle both json and base64 as body types
        try:
            logger.debug("decoding body as base64...")
            json_body = b64decode(request.data)
            request_data = json.loads(json_body)
            logger.debug("SUCCESS!")
        except ValueError:
            logger.debug("FAILED!")
            logger.debug("decoding body as json...")
            request_data = request.get_json(force=True)
            logger.debug("SUCCESS!")

        if authorization_function:
            # None allows the request; any other return value is the rejection response
            rejection = authorization_function(request_data)
            if rejection is not None:
                logger.info("config['JOBREQUEST_AUTHORIZATION_FUNCTION'] rejected job request")
                return rejection

        if transform_function:
            # applied before validation so server-injected fields are validated
            request_data = transform_function(request_data)

        try:
            validated_data = RequestPostBodyModel(**request_data)
        except ValidationError as e:
            logger.exception("bad request")
            response_content = {"message": f"invalid input: {e.args}"}
            status_code = HTTPStatus.UNPROCESSABLE_ENTITY
        else:
            response_content, status_code = _create_processingjob(validated_data)
        logger.debug("%d response_content=%s", status_code, response_content)
        return response_content, status_code

    @app.route(f"{endpoint_prefix}/healthcheck", methods=("get",))
    @authorizationdecorator
    def healthcheck() -> tuple[dict, int]:
        """Provide a method to confirm deployment is functional"""
        return {"status": "OK"}, HTTPStatus.OK

    if SettingsModel:
        check_model_defaults(SettingsModel)  # raises exception if error

        @app.route(f"{endpoint_prefix}/settings", methods=("PATCH",))
        @authorizationdecorator
        @api.validate(
            headers=Headers,
            body=SpecRequest(SettingsModel),
            resp=SpecResponse(
                HTTP_200=SettingsModel,
                HTTP_400=BadRequest400Response,
                HTTP_401=None,
                HTTP_429=TooManyRequests429Response,
            ),
            tags=["settings"],
            before=fps_error_logger,
            after=fps_error_logger,
        )
        def settings_patch() -> tuple[dict, int]:
            """システム設定を更新"""
            logger.debug("request.data=%s", request.data)
            # apigateway may send requests as base64
            # -> Handle both json and base64 as body types
            try:
                logger.debug("decoding body as base64...")
                json_body = b64decode(request.data)
                request_data = json.loads(json_body)
                logger.debug("SUCCESS!")
            except ValueError:
                logger.debug("FAILED!")
                logger.debug("decoding body as json...")
                request_data = request.get_json(force=True)
                logger.debug("SUCCESS!")

            try:
                logger.debug("request_data=%s", request_data)
                validated_data = SettingsModel(**request_data)
            except ValidationError as e:
                return {
                    "message": f"ERROR - invalid input for request: {e.args}"
                }, HTTPStatus.UNPROCESSABLE_ENTITY.value

            try:
                logger.info(f"retrieving SettingsModel(SETTINGS_ID={settings.SETTINGS_ID})...")
                item: ProcessingSettingsModel = ProcessingSettingsModel.get_processingsettingsmodel_item(
                    settings_id=settings.SETTINGS_ID, as_dict=False
                )
            except ItemDoesNotExistError as e:
                logger.exception(f"SETTINGS_ID({settings.SETTINGS_ID}) not found: {e.args}")
                return {"message": f"SETTINGS_ID({settings.SETTINGS_ID}) not found"}, HTTPStatus.NOT_FOUND.value
            except (QueryError, VerboseClientError) as e:
                logger.exception(e.args)
                if not any(
                    error_message in str(e.args)
                    for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
                ):
                    raise  # re-raise QueryError Exception
                return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

            patch_data = validated_data.model_dump()
            current_settings = item.as_dict()
            updated_settings = current_settings
            updated_settings.update(patch_data)

            logger.info(f"updating ProcessingSettingsModel(SETTINGS_ID={settings.SETTINGS_ID})...")
            current_timestamp_utc = get_timestamp_now()
            try:
                logger.debug(f"updated_settings={updated_settings}")
                item.update(
                    actions=[
                        ProcessingSettingsModel.settings.set(updated_settings),
                        ProcessingSettingsModel.updated_timestamp.set(current_timestamp_utc),
                    ]
                )
            except (QueryError, VerboseClientError) as e:
                logger.exception(e.args)
                if not any(
                    error_message in str(e.args)
                    for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
                ):
                    raise  # re-raise QueryError Exception
                return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value
            logger.info(f"updating ProcessingSettingsModel(SETTINGS_ID={settings.SETTINGS_ID})... DONE")

            # retrieve the updated item
            item: dict = ProcessingSettingsModel.get_processingsettingsmodel_item(
                settings_id=settings.SETTINGS_ID, as_dict=True
            )
            logger.debug(f"item={item}")
            return item, HTTPStatus.ACCEPTED.value

        @app.route(f"{endpoint_prefix}/settings", methods=("GET",))
        @authorizationdecorator
        @api.validate(
            headers=Headers,
            body=SpecRequest(SettingsModel),
            resp=SpecResponse(
                HTTP_200=SettingsModel,
                HTTP_400=BadRequest400Response,
                HTTP_401=None,
                HTTP_429=TooManyRequests429Response,
            ),
            tags=["settings"],
            before=fps_error_logger,
            after=fps_error_logger,
        )
        def settings_get() -> tuple[dict, int]:
            """システム設定を取得"""
            try:
                item = get_latest_settings()
            except ItemDoesNotExistError as e:
                logger.exception(f"SETTINGS_ID({settings.SETTINGS_ID}) not found: {e.args}")
                return {"message": f"SETTINGS_ID({settings.SETTINGS_ID}) not found"}, HTTPStatus.NOT_FOUND.value
            except (QueryError, VerboseClientError) as e:
                logger.exception(e.args)
                if not any(
                    error_message in str(e.args)
                    for error_message in ("ThrottlingException", "ProvisionedThroughputExceededException")
                ):
                    raise  # re-raise QueryError Exception
                return {"message": str(e.args)}, HTTPStatus.TOO_MANY_REQUESTS.value

            return item, HTTPStatus.OK.value

    @app.route("/openapi", methods=("get",))
    @authorizationdecorator
    def serve_openapi() -> tuple[str, int]:
        """Serve openapi (swagger) spec for reference"""
        # load template
        template_filepath = STATIC_ASSETS_DIRECTORY / "templates" / "openapi.html.template"
        with template_filepath.open("r", encoding="utf8") as t:  # pylint: disable=invalid-name
            template = Template(t.read())

        rendered = template.substitute(
            title=api_title,
            spec=json.dumps(api.spec),
        )
        return rendered, HTTPStatus.OK

    @app.errorhandler(HTTPStatus.NOT_FOUND)
    def page_not_found(_: Any) -> tuple[Iterable, int]:  # noqa: ANN401
        """Force 404 for undefined endpoint requests"""
        return f"{request.base_url}", HTTPStatus.NOT_FOUND

    api.register(app)
    return app
