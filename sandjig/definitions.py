import logging
import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ValidationError, ValidationInfo, constr, field_validator, model_validator

from .settings import (
    AWS_DEFAULT_REGION,
    AWS_PROFILE,
    BASIC_AUTH_PASSWORD,
    BASIC_AUTH_USERNAME,
    DYNAMODB_PROCESSINGJOB_REQUESTS_TABLENAME,
    DYNAMODB_PROCESSINGJOB_SETTINGS_TABLENAME,
    DYNAMODB_SORTINDEX_INDEXNAME,
    SQSQUEUE_VISIBILITYTIMEOUT,
)

S3_BUCKET_NAME_PATTERN = re.compile(
    r"^(?!.*\.\.)(?!.*\.-|.*-\.)(?!^\d+\.\d+\.\d+\.\d+$)[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
)


logger = logging.getLogger(__name__)


class StageValues(str, Enum):
    """Defines the valid 'stage' values."""

    STAGE = "stg"
    PRODUCTION = "prd"
    DEVELOPMENT = "dev"


class ValidPythonRuntimes(str, Enum):
    """Define the validation model for python version for SandjigSettingParameters' Runtime"""

    PYTHON_314 = "python3.14"


class SandjigAppStackParameters(BaseModel):
    """Define the validation model for the parameters needed to build an sandjig application stack"""

    Prefix: constr(min_length=3, max_length=10) = "sandjig-"
    PythonRuntime: ValidPythonRuntimes = ValidPythonRuntimes.PYTHON_314
    UniqueSuffix: constr(min_length=3, max_length=8)
    APIGatewayStage: StageValues = StageValues.DEVELOPMENT
    SourceS3BucketName: str | None = None
    SandjigAuthorizerZipPackageLocalPath: Path | None = None
    SandjigFunctionZipPackageLocalPath: Path
    ProcessingJobSQSQueueVisibilityTimeout: int = SQSQUEUE_VISIBILITYTIMEOUT
    DynamodbRequestsTableName: str = DYNAMODB_PROCESSINGJOB_REQUESTS_TABLENAME
    DynamodbSettingsTableName: str = DYNAMODB_PROCESSINGJOB_SETTINGS_TABLENAME
    DyanmodbSortIndexName: str = DYNAMODB_SORTINDEX_INDEXNAME
    BasicAuthUsername: str = BASIC_AUTH_USERNAME
    BasicAuthPassword: str = BASIC_AUTH_PASSWORD

    def get_sandjiguserpackage_name(self) -> str:
        return self.SandjigFunctionZipPackageLocalPath.name

    def get_sandjigauthorizorpackage_name(self) -> str:
        return self.SandjigAuthorizerZipPackageLocalPath.name

    @field_validator("Prefix", mode="before")
    def validate_prefix(cls, v: str) -> str:  # noqa: N805
        v = v.removesuffix("-")
        return v

    @field_validator("UniqueSuffix", mode="before")  # noqa: N805
    def validate_suffix(cls, v: str) -> str:  # noqa: N805
        v = v.removeprefix("-")
        return v

    @field_validator("SourceS3BucketName")
    def validate_sources3bucketname(cls, v: str | None) -> str | None:  # noqa: N805
        if v and v.strip() and S3_BUCKET_NAME_PATTERN.match(v):
            return v
        raise ValidationError(f"Invalid S3 bucket name: {v}. Must match pattern: {S3_BUCKET_NAME_PATTERN.pattern}")

    @model_validator(mode="after")
    def set_sources3bucketname_default(self, _: ValidationInfo):
        if self.SourceS3BucketName is None or not self.SourceS3BucketName.strip():
            bucket_name = f"{self.Prefix}-sdsrc-{self.UniqueSuffix}-{self.APIGatewayStage.value}"
            logger.warning(f"SourceS3BucketName not given, setting default: '{bucket_name}'")
            self.validate_sources3bucketname(bucket_name)
            self.SourceS3BucketName = bucket_name
        return self

    def get_stack_name(self) -> str:
        return f"{self.Prefix}-sdj-{self.UniqueSuffix}-{self.APIGatewayStage.value}"

    def model_dump(self, *args, **kwargs) -> dict:
        d = super().model_dump(*args, **kwargs)
        del d["SandjigAuthorizerZipPackageLocalPath"]
        del d["SandjigFunctionZipPackageLocalPath"]
        d["SandjigUserPackageName"] = self.get_sandjiguserpackage_name()
        d["SandjigAuthorizerPackageName"] = self.get_sandjigauthorizorpackage_name()
        return d


class SandjigAppDestroyParameters(BaseModel):
    """Define the validation model for the parameters needed to destroy an sandjig application stack"""

    Prefix: constr(min_length=3, max_length=10) = "sandjig-"
    UniqueSuffix: constr(min_length=3, max_length=8)
    SourceS3BucketName: str | None = None

    @field_validator("SourceS3BucketName")
    def validate_sources3bucketname(cls, v: str | None) -> str | None:  # noqa: N805
        if S3_BUCKET_NAME_PATTERN.match(v):
            return v
        raise ValidationError(f"Invalid S3 bucket name: {v}. Must match pattern: {S3_BUCKET_NAME_PATTERN.pattern}")


class SandjigSettingParameters(BaseModel):
    """Define the validation model for the parameters needed to build an sandjig initial settings"""

    AppPath: Path
    AwsRegion: str = AWS_DEFAULT_REGION
    AwsProfile: str = AWS_PROFILE
    Runtime: ValidPythonRuntimes = ValidPythonRuntimes.PYTHON_314.value

    def _is_package(self, p: Path) -> bool:
        if p.is_dir():
            for pfile in p.glob("*.py"):
                if pfile.name == "__init__.py":
                    return True
        return False

    def get_app_function(self) -> str:
        assert self.AppPath.exists()
        parent = self.AppPath.parent
        components = []
        while self._is_package(parent):
            components.append(parent.name)
            # get next directory up
            parent = parent.parent
        components.append(self.AppPath.stem)

        # determine sandjig app variable name in given file
        createapp_identifiers = ("= create_app(", "=create_app(")
        app_content = self.AppPath.read_text(encoding="utf8")
        sandjig_appname_candidate_lines = [
            line.strip()
            for line in app_content.split("\n")
            if any(identifier in line for identifier in createapp_identifiers)
        ]
        sandjig_appname = None
        for candidate_line in sandjig_appname_candidate_lines:
            name = candidate_line.split("=")[0].strip()
            if sandjig_appname:
                assert name == sandjig_appname, f"sandjig appname candidates not equal: {name} != {sandjig_appname}"
            sandjig_appname = name
        if not sandjig_appname:
            raise ValueError(f"file does not contain expected 'create_app()': {self.AppPath.absolute()}")
        components.append(sandjig_appname)
        return ".".join(components)


class ValidCloudformationActions(str, Enum):
    """Supported Cloudformation actions"""

    CREATE = "create"
    UPDATE = "update"
