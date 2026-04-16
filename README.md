# sandjig

Agent-native job state management API service for async APIs.

Ported from [aframax](https://github.com/kiconiaworks/aframax) with Python 3.14, uv, and latest dependencies.


## Usage

Define your request/response payload models and create the Flask app:

```python
from sandjig import create_app
from sandjig.models import RequestPostPayloadBaseModel, ResponsePostPayloadBaseModel


class MyRequestPostPayload(RequestPostPayloadBaseModel):
   examplevalue: str


class MyResponsePostPayload(ResponsePostPayloadBaseModel):
    result_value: int


app = create_app(MyRequestPostPayload, MyResponsePostPayload, config={})
```

### With settings

```python
from sandjig import create_app
from sandjig.models import RequestPostPayloadBaseModel, ResponsePostPayloadBaseModel, SettingsBaseModel


class MyRequestPostPayload(RequestPostPayloadBaseModel):
   examplevalue: str


class MyResponsePostPayload(ResponsePostPayloadBaseModel):
    result_value: int


class MySettings(SettingsBaseModel):
    adjust: float = 0.5


app = create_app(MyRequestPostPayload, MyResponsePostPayload, MySettings, config={})
```

### Deploy

```bash
export AWS_PROFILE={profile}
export AWS_DEFAULT_REGION={region}
export BASIC_AUTH_USERNAME={username}
export BASIC_AUTH_PASSWORD={password}
sandjig deploy -s {SUFFIX} -n {APP_PYTHON_FILE} --stage {stg|dev|prd}
```


## API Endpoints

> If `ENDPOINT_PREFIX` is set in config, it prepends `/jobs` and `/settings` endpoints.
> Example: `ENDPOINT_PREFIX="/api"` produces `/api/jobs`.

### `/jobs`

- `POST /jobs` - Submit a new job
- `GET /jobs` - List jobs (paginated)
- `GET /jobs/{JOB_ID}` - Get job status
- `PATCH /jobs/{JOB_ID}` - Update job status

#### GET query parameters

| Parameter | Description |
|---|---|
| `limit` | Items per page (50-500, default 250) |
| `job_id` | Comma-separated UUIDs to filter (max 175) |
| `status` | Filter by status (pending, queued, validating, processing, completed, error, cancelled) |
| `registered_datetime_gte` | ISO-8601 datetime lower bound |
| `registered_datetime_lte` | ISO-8601 datetime upper bound |

### `/settings` (optional)

Only available when a `SettingsBaseModel` subclass is passed to `create_app()`.

- `GET /settings`
- `PATCH /settings`

### Other endpoints

- `GET /openapi` - Swagger UI
- `GET /openapi/schema` - OpenAPI YAML spec
- `GET /healthcheck` - 200 OK health check

### Job response (example)

```json
{
   "job_id": "{JOB_ID}",
   "registered_datetime": "2026-04-16T14:53:18+09:00",
   "updated_datetime": "2026-04-16T14:53:20+09:00",
   "completed_datetime": null,
   "status": "pending",
   "result_count": 0,
   "settings": null,
   "request_payload": {
      "examplevalue": "my example"
   },
   "response_payload": null
}
```


## `create_app` config options

| Field | Description |
|---|---|
| `API_TITLE` | Display title for OpenAPI UI |
| `API_VERSION` | OpenAPI displayed version |
| `BASIC_AUTH_FORCE` | When `True`, basic auth is required. `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` env vars must be set. |
| `BASIC_AUTH_USERNAME` | Username for basic auth (when `BASIC_AUTH_FORCE=True`) |
| `BASIC_AUTH_PASSWORD` | Password for basic auth (when `BASIC_AUTH_FORCE=True`) |
| `SQS_QUEUE_URL` | If set, job requests are sent as messages to this SQS queue |
| `ENDPOINT_PREFIX` | Prefix for `/jobs` endpoints (must start with `/`) |
| `JOBREQUEST_CALLBACK_FUNCTION` | Callable invoked on successful job request with `job_id` argument |
| `JSON_AS_ASCII` | If `True`, JSON dumped as ASCII (default `False`) |


## CLI Commands

```bash
sandjig deploy [-h] [-b BUCKET] [--stage STAGE] -n APPNAME
sandjig update [-h] [-b BUCKET] [--stage STAGE] -n APPNAME
sandjig destroy [-h]
sandjig package [-h] [--stage STAGE] -n APPNAME -o OUTPUT_DIRECTORY
sandjig template [-h] [-o OUTPUT]
```


## Local Development

Python 3.14+ with [uv](https://docs.astral.sh/uv/).

### Setup

```bash
uv sync
```

### Run tests

Requires localstack (docker-compose):

```bash
docker compose up -d
uv run pytest -v
```

### Linting

```bash
uv run ruff check
```

### Type checking

```bash
uv run pyright
```


## Environment Variables

Required for local development (`.env`):

```
AWS_ACCOUNT_ID=dummyid
S3_SERVICE_ENDPOINT=http://localhost:4566
SQS_SERVICE_ENDPOINT=http://localhost:4566
STS_SERVICE_ENDPOINT=http://localhost:4566
DYNAMODB_SERVICE_ENDPOINT=http://localhost:4566
```
