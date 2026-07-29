"""Tests for scoped lazy DynamoDB table creation (#22)."""

from http import HTTPStatus

import pytest

from sandjig import create_app
from sandjig.jobsapi import api as api_module

from ...utils import TestRequestPostPayloadModel, TestResponsePostPayloadModel


class CreateResourcesRecorder:
    """Concrete fake for create_dynamodb_resources — records calls."""

    def __init__(self) -> None:
        self.calls: int = 0

    def __call__(self, *args, **kwargs) -> None:
        self.calls += 1


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> CreateResourcesRecorder:
    fake = CreateResourcesRecorder()
    monkeypatch.setattr(api_module, "create_dynamodb_resources", fake)
    return fake


def test_embedder_route_does_not_trigger_table_creation(recorder: CreateResourcesRecorder) -> None:
    """Routes registered by an embedding app (e.g. /health) must not touch DynamoDB (#22)."""
    app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)

    @app.route("/embedder-health")
    def embedder_health() -> dict:
        return {"status": "ok"}

    client = app.test_client()
    response = client.get("/embedder-health")

    assert response.status_code == HTTPStatus.OK
    assert recorder.calls == 0


def test_healthcheck_route_does_not_trigger_table_creation(recorder: CreateResourcesRecorder) -> None:
    app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
    client = app.test_client()
    response = client.get("/healthcheck")

    assert response.status_code == HTTPStatus.OK
    assert recorder.calls == 0


def test_jobs_route_triggers_table_creation_once(recorder: CreateResourcesRecorder) -> None:
    app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel)
    client = app.test_client()

    client.get("/jobs")
    client.get("/jobs")

    assert recorder.calls == 1


def test_jobs_route_with_endpoint_prefix_triggers_table_creation(recorder: CreateResourcesRecorder) -> None:
    app = create_app(TestRequestPostPayloadModel, TestResponsePostPayloadModel, config={"ENDPOINT_PREFIX": "/api"})
    client = app.test_client()

    client.get("/api/jobs")

    assert recorder.calls == 1


def test_skip_table_create_config_disables_creation(recorder: CreateResourcesRecorder) -> None:
    """Resources-only deployments (tables managed by CFn) can disable creation entirely (#22)."""
    app = create_app(
        TestRequestPostPayloadModel, TestResponsePostPayloadModel, config={"SKIP_TABLE_CREATE": True}
    )
    client = app.test_client()

    client.get("/jobs")

    assert recorder.calls == 0
