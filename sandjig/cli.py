import json
import logging
import re
import subprocess
import sys
import tempfile
import warnings
from argparse import Namespace
from pathlib import Path
from shutil import copyfile, make_archive

from botocore.exceptions import ClientError

from .aws import CFN_CLIENT, DYNAMODB_CLIENT, S3_CLIENT, S3_RESOURCE
from .definitions import (
    SandjigAppStackParameters,
    SandjigSettingParameters,
    StageValues,
    ValidCloudformationActions,
    ValidPythonRuntimes,
)
from .jobsapi import __version__ as JOBSAPI_VERSION  # noqa: N812
from .settings import (
    AWS_DEFAULT_REGION,
    AWS_PROFILE,
    BASIC_AUTH_PASSWORD,
    BASIC_AUTH_USERNAME,
    DEFAULT_APP_STAGE,
    DEFAULT_TEMPLATE_OUTPUT_FILEPATH,
    INDEXNAME_ROOT,
    REQUESTS_TABLENAME_ROOT,
    SANDJIG_SUFFIX,
    SETTINGS_TABLENAME_ROOT,
    TEMPLATE_FILEPATH,
)

logger = logging.getLogger(__name__)


def prepare_source_bucket(
    bucket_name: str, app_package_path: Path, authorizer_package_path: Path, region: str = AWS_DEFAULT_REGION
) -> None:
    """Create the application source package bucket."""
    logger.info(f"Creating bucket ({bucket_name}) region={region}...")
    try:
        response = S3_CLIENT.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": region})
        logger.debug(response)
        logger.info(f"Creating bucket ({bucket_name}) ... SUCCESS")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            logger.info(f"Creating bucket ({bucket_name}) ... {e.response['Error']['Code']}")
        else:
            raise

    # upload source packages
    for package_localpath in (app_package_path, authorizer_package_path):
        assert package_localpath.exists(), f"Expected file not found: {package_localpath}"
        logger.debug(f"Uploading {package_localpath} to s3:/{bucket_name}/{package_localpath.name} ...")
        with package_localpath.open("rb") as f:
            S3_RESOURCE.Bucket(bucket_name).upload_fileobj(f, package_localpath.name)
        logger.debug(f"Uploading {package_localpath} to s3:/{bucket_name}/{package_localpath.name} ... DONE")


def create_authorizer_package(output_directory: Path, filename: str = "sandjig-basicauth-authorizer") -> Path:
    """Create the authorizer zip package"""
    filepath = output_directory / filename
    root_directory = Path(__file__).parent
    base_directory = "apigwauthorizers"
    return Path(make_archive(str(filepath), format="zip", root_dir=str(root_directory), base_dir=base_directory))


def create_deployment_package(stage: str, output_directory: Path | None = None) -> Path:
    """Create zappa package for an sandjig application."""
    args = ["zappa", "package", stage]
    if output_directory:
        default_application_package_name = "backend-api.zip"
        output_filepath = output_directory / default_application_package_name
        logger.warning(f"Default application package name used: {default_application_package_name}")
        args.extend(["-o", str(output_filepath)])

    output = subprocess.check_output(args)  # noqa: S603
    # find created zappa package filename from output text
    match = re.search(r"Package created: .*.zip", output.decode())
    if not match:
        raise ValueError("Could not find package filename in zappa output")
    zip_filename = match.group().replace("Package created: ", "")
    deployment_zip_filepath = Path(zip_filename)

    assert deployment_zip_filepath.exists()
    logger.info(f"Created zappa_package zip: {deployment_zip_filepath}")

    return deployment_zip_filepath


def deploy(*args, **kwargs) -> tuple[int, dict, str]:
    """Deploy wrapper for _execute_cloudformation_command()"""
    action = ValidCloudformationActions.CREATE
    return _execute_cloudformation_command(action, *args, **kwargs)


def update(*args, **kwargs) -> tuple[int, dict, str]:
    """Update wrapper for _execute_cloudformation_command()"""
    action = ValidCloudformationActions.UPDATE
    return _execute_cloudformation_command(action, *args, **kwargs)


def _execute_cloudformation_command(
    action: ValidCloudformationActions,
    stackparameters: SandjigAppStackParameters,
    region: str = AWS_DEFAULT_REGION,
    template_filepath: Path = TEMPLATE_FILEPATH,
) -> tuple[int, dict, str]:
    """Build and deploy/update the application stack for an sandjig application."""
    stack_name = stackparameters.get_stack_name()
    assert template_filepath.exists()
    bucket_name = stackparameters.SourceS3BucketName
    if bucket_name is None:
        raise ValueError("SourceS3BucketName is required")

    authorizer_path = stackparameters.SandjigAuthorizerZipPackageLocalPath
    if authorizer_path is None:
        raise ValueError("SandjigAuthorizerZipPackageLocalPath is required")

    prepare_source_bucket(
        bucket_name,
        stackparameters.SandjigFunctionZipPackageLocalPath,
        authorizer_path,
        region,
    )

    parameters = []
    cfn_cli_parameters = []
    sam_cli_parameters = []
    for k, v in stackparameters.model_dump().items():
        parameter = {"ParameterKey": k, "ParameterValue": f"{v}"}
        parameters.append(parameter)
        cfn_cli_parameters.append(f"ParameterKey={k},ParameterValue={v}")
        sam_value = v
        if k.endswith("LocalPath"):
            sam_value = str(v).replace("file://", "")  # remove
        sam_cli_parameters.append(f"{k}={sam_value}")

    cfn_cli_command = (
        f"aws cloudformation {action.value}-stack "
        f"--stack-name {stack_name} "
        f"--template-body file://{template_filepath} "
        f"--parameters {' '.join(cfn_cli_parameters)} "
        f"--capabilities CAPABILITY_AUTO_EXPAND CAPABILITY_IAM"
    )
    logger.info(f"awscli command: {cfn_cli_command}")

    sam_cli_command = (
        f"sam deploy "
        f"--stack-name {stack_name} "
        f"--template-file {TEMPLATE_FILEPATH} "
        f"--parameter-overrides {' '.join(sam_cli_parameters)} "
        f"--capabilities CAPABILITY_IAM"
    )
    logger.info(f"sam command: {sam_cli_command}")
    logger.info(f"{action.value}_stack() parameters: {parameters}")
    logger.info(f"{action.value} stack ({stack_name}) ...")
    action_response = getattr(CFN_CLIENT, f"{action.value}_stack")(
        StackName=stack_name,
        TemplateBody=TEMPLATE_FILEPATH.read_text(),
        DisableRollback=True,
        Parameters=parameters,
        Capabilities=["CAPABILITY_AUTO_EXPAND", "CAPABILITY_IAM"],
    )
    result_statuscode = action_response["ResponseMetadata"]["HTTPStatusCode"]

    waiter = CFN_CLIENT.get_waiter(f"stack_{action.value}_complete")
    check_wait_seconds = 10
    max_attempts = 120
    logger.debug(
        f"{action.value} ({stack_name}) ... "
        f"(WAITING check_wait_seconds={check_wait_seconds}, max_attempts={max_attempts})"
    )
    waiter.wait(StackName=stack_name, WaiterConfig={"Delay": check_wait_seconds, "MaxAttempts": max_attempts})

    describestacks_response = CFN_CLIENT.describe_stacks(StackName=stack_name)
    assert len(describestacks_response["Stacks"]) == 1
    outputs = describestacks_response["Stacks"][0]["Outputs"]
    deployed_apigw_url = None
    for output in outputs:
        key = output.get("OutputKey")
        if key == "AWSApiGatewayWithBasicAuthUrl":
            deployed_apigw_url = output["OutputValue"]
            break

    if deployed_apigw_url is None:
        raise ValueError("Could not find AWSApiGatewayWithBasicAuthUrl in stack outputs")

    logger.info(f"Deployed APIGW Url: {deployed_apigw_url}")
    return result_statuscode, action_response, deployed_apigw_url


def create_settings_file(settingparameters: SandjigSettingParameters, stage: StageValues) -> Path:
    """Create zappa_settings.json for an sandjig application."""
    zappa_settings = {
        stage: {
            "app_function": settingparameters.get_app_function(),
            "aws_region": settingparameters.AwsRegion,
            "profile_name": settingparameters.AwsProfile,
            "project_name": "sandjig-project",
            "runtime": settingparameters.Runtime.value,
            "s3_bucket": "s3-bucket",
            "use_apigateway": False,
        }
    }
    setting_filepath = Path("zappa_settings.json").resolve()
    with setting_filepath.open("w", encoding="utf8") as f:
        json.dump(zappa_settings, f)
    logger.info(f"Created 'zappa_settings.json' file: {setting_filepath}")
    logger.info(zappa_settings)

    assert setting_filepath.exists()
    return setting_filepath


def destroy_stack(destroy_stack_name: str) -> None:
    """Destroy the application CloudFormation Stack for an sandjig application."""
    logger.debug(f"Deleting {destroy_stack_name} ...")
    CFN_CLIENT.delete_stack(StackName=destroy_stack_name)
    waiter = CFN_CLIENT.get_waiter("stack_delete_complete")
    check_wait_seconds = 10
    max_attempts = 120
    logger.debug(
        f"Deleting {destroy_stack_name} ... "
        f"(WAITING check_wait_seconds={check_wait_seconds}, max_attempts={max_attempts})"
    )
    waiter.wait(StackName=destroy_stack_name, WaiterConfig={"Delay": check_wait_seconds, "MaxAttempts": max_attempts})
    logger.debug(f"Deleting {destroy_stack_name} ...SUCCESS")


def destroy_bucket_objects(bucket_name: str, bucket_keys: list) -> None:
    """Destroy Objects in the application S3 Bucket for an sandjig application."""
    logger.debug(f"Deleting objects in {bucket_name} ...")
    keys_to_delete = {"Objects": [{"Key": key} for key in bucket_keys]}
    S3_CLIENT.delete_objects(Bucket=bucket_name, Delete=keys_to_delete)
    logger.debug(f"Deleting objects in {bucket_name} ...SUCCESS")


def destroy_bucket(bucket_name: str) -> None:
    """Destroy the application S3 Bucket for an sandjig application."""
    logger.debug(f"Deleting bucket '{bucket_name}' ...")
    S3_CLIENT.delete_bucket(Bucket=bucket_name)
    logger.debug(f"Deleting bucket '{bucket_name}' ...SUCCESS")


def destroy_dynamodb_tables(tablenames: list[str]) -> None:
    """Destroy the sandjig application related tablenames."""
    for tablename in tablenames:
        logger.debug(f"Deleting table '{tablename}' ...")
        response = DYNAMODB_CLIENT.delete_table(TableName=tablename)
        logger.debug(response)
        logger.debug(f"Deleting table '{tablename}' ...SUCCESS")


def list_bucket_objects(bucket_name: str) -> list[str]:
    """Check existence of the application S3 Bucket Objects for an sandjig application."""
    s3_list_response = S3_CLIENT.list_objects_v2(Bucket=bucket_name)
    contents = s3_list_response["Contents"]
    objects_keys = [content["Key"] for content in contents]

    return objects_keys


def list_dynamodb_tablenames(stack_name: str) -> list[str]:
    """Get dynamodb tablenames with given prefix matching expected table/index names."""
    sandjig_tables = []

    # get configured table/index names from stack parameters
    response = CFN_CLIENT.describe_stacks(StackName=stack_name)
    assert "Stacks" in response, f"error ('Stacks' not in response): {response}"
    assert len(response["Stacks"]) == 1, f"found ({len(response['Stacks'])}): stack_name={stack_name}"

    stack_info = response["Stacks"][0]

    # defined in sandjig/cloudformation/app.sam.yaml
    expected_table_parameter_keys = ("DynamodbRequestsTableName", "DyanmodbSortIndexName")
    # get set table/index names from stack_info
    assert "Parameters" in stack_info
    expected_tablenames = []
    for parameter_definition in stack_info["Parameters"]:
        if parameter_definition["ParameterKey"] in expected_table_parameter_keys:
            tablename = parameter_definition["ParameterValue"]
            expected_tablenames.append(tablename)
    logger.debug(f"expected_tablenames={expected_tablenames}")

    response = DYNAMODB_CLIENT.list_tables()
    for tablename in response["TableNames"]:
        if tablename in expected_tablenames:
            sandjig_tables.append(tablename)

    while "LastEvaluatedTableName" in response:
        last_evaluated_tablename = response["LastEvaluatedTableName"]
        response = DYNAMODB_CLIENT.list_tables(ExclusiveStartTableName=last_evaluated_tablename)
        for tablename in response["TableNames"]:
            if tablename in expected_tablenames:
                sandjig_tables.append(tablename)
    return sandjig_tables


def list_stack_resources(stack_name: str) -> list[str]:
    """Get resources of sandjig application stack"""
    cfn_resource_response = CFN_CLIENT.describe_stack_resources(StackName=stack_name)
    resource_list = [f"{r['ResourceType']} {r['PhysicalResourceId']}" for r in cfn_resource_response["StackResources"]]

    return resource_list


def copy_cfn_template_file(src: Path, target: Path):
    """Copy cloudformation template file"""
    logger.info(f"copy cloudformation template file to {target}")
    copyfile(src, target)
    logger.info("Finish.")


def filepath(value: str) -> Path:
    """Define the argparse type for a file Path object"""
    p = Path(value).absolute()
    assert p.exists(), f"FileNotFound: {p}"
    return p


def directory_path(value: str) -> Path:
    """Define the argparse type for a directory Path object"""
    p = Path(value).absolute()
    assert p.exists(), f"directory not found: {value}"
    assert p.is_dir(), f"path is not a directory: {value}"
    return p


def yaml_filepath(value: str) -> Path:
    """Define the argparse type for a Path object of yaml file"""
    p = Path(value)
    if p.suffix not in [".yaml", ".yml"]:
        warnings.warn(
            "Template file is yaml file. The recommended extension is '.yaml' or '.yml'", SyntaxWarning, stacklevel=2
        )

    return p


def _handle_deploy_command(args: Namespace, user_runtime: ValidPythonRuntimes) -> None:
    parameters = SandjigSettingParameters(
        AppPath=args.appname, AwsRegion=AWS_DEFAULT_REGION, AwsProfile=AWS_PROFILE, Runtime=user_runtime
    )
    create_settings_file(parameters, stage=args.stage)

    deployment_package_filepath = create_deployment_package(args.stage)
    dynamodb_requests_tablename = f"{args.prefix}{REQUESTS_TABLENAME_ROOT}-{args.stage}"
    dynamodb_settings_tablename = f"{args.prefix}{SETTINGS_TABLENAME_ROOT}-{args.stage}"
    dynamodb_sort_indexname = f"{args.prefix}{INDEXNAME_ROOT}-{args.stage}"
    with tempfile.TemporaryDirectory() as tempdir:
        authorizer_filepath = create_authorizer_package(output_directory=Path(tempdir))
        parameters = SandjigAppStackParameters(
            Prefix=args.prefix,
            PythonRuntime=user_runtime,
            UniqueSuffix=args.suffix,
            APIGatewayStage=args.stage,
            SourceS3BucketName=args.bucket,
            DynamodbRequestsTableName=dynamodb_requests_tablename,
            DynamodbSettingsTableName=dynamodb_settings_tablename,
            DyanmodbSortIndexName=dynamodb_sort_indexname,
            BasicAuthUsername=BASIC_AUTH_USERNAME,
            BasicAuthPassword=BASIC_AUTH_PASSWORD,
            SandjigAuthorizerZipPackageLocalPath=authorizer_filepath,
            SandjigFunctionZipPackageLocalPath=deployment_package_filepath,
        )

        createstack_result_statuscode, createstack_response, deployed_apigw_url = deploy(parameters)
        successful_response_codes_start = 200
        successful_response_codes_end = 299
        if successful_response_codes_start <= createstack_result_statuscode <= successful_response_codes_end:
            logger.info(f"deploy SUCCESS({createstack_result_statuscode})!")
            logger.info(f"Deployed to: {deployed_apigw_url}")
        else:
            logger.error(f"deploy ERROR({createstack_result_statuscode})!")
            logger.error(createstack_response)


def _handle_update_command(args: Namespace, user_runtime: ValidPythonRuntimes) -> None:
    parameters = SandjigSettingParameters(
        AppPath=args.appname, AwsRegion=AWS_DEFAULT_REGION, AwsProfile=AWS_PROFILE, Runtime=user_runtime
    )
    create_settings_file(parameters, stage=args.stage)

    deployment_package_filepath = create_deployment_package(args.stage)
    dynamodb_requests_tablename = f"{args.prefix}{REQUESTS_TABLENAME_ROOT}-{args.stage}"
    dynamodb_settings_tablename = f"{args.prefix}{SETTINGS_TABLENAME_ROOT}-{args.stage}"
    dynamodb_sort_indexname = f"{args.prefix}{INDEXNAME_ROOT}-{args.stage}"
    with tempfile.TemporaryDirectory() as tempdir:
        authorizer_filepath = create_authorizer_package(output_directory=Path(tempdir))
        parameters = SandjigAppStackParameters(
            Prefix=args.prefix,
            PythonRuntime=user_runtime,
            UniqueSuffix=args.suffix,
            APIGatewayStage=args.stage,
            SourceS3BucketName=args.bucket,
            DynamodbRequestsTableName=dynamodb_requests_tablename,
            DynamodbSettingsTableName=dynamodb_settings_tablename,
            DyanmodbSortIndexName=dynamodb_sort_indexname,
            BasicAuthUsername=BASIC_AUTH_USERNAME,
            BasicAuthPassword=BASIC_AUTH_PASSWORD,
            SandjigAuthorizerZipPackageLocalPath=authorizer_filepath,
            SandjigFunctionZipPackageLocalPath=deployment_package_filepath,
        )

        updatestack_result_statuscode, updatestack_response, deployed_apigw_url = update(parameters)
        successful_response_codes_start = 200
        successful_response_codes_end = 299
        if successful_response_codes_start <= updatestack_result_statuscode <= successful_response_codes_end:
            logger.info(f"update SUCCESS({updatestack_result_statuscode})!")
            logger.info(f"Updated: {deployed_apigw_url}")
        else:
            logger.error(f"update ERROR({updatestack_result_statuscode})!")
            logger.error(updatestack_response)


def _handle_destroy_command(args: Namespace) -> None:
    bucket_name = f"{args.prefix}sandjig-src{args.suffix}"
    stack_name = f"{args.prefix}sandjig-stack{args.suffix}"

    bucket_objects = list_bucket_objects(bucket_name=bucket_name)
    bucket_objects_text = "\n".join(f"  s3://{bucket_name}/{key}" for key in sorted(bucket_objects))

    dynamodb_tablenames = list_dynamodb_tablenames(stack_name=stack_name)
    dynamodb_tablenames_text = "\n".join(dynamodb_tablenames)

    stack_resources = list_stack_resources(stack_name=stack_name)
    stack_resources_text = "\n".join(f"  stack/{stack_name}/{resource}" for resource in sorted(stack_resources))
    logger.info(
        """==============\n"""
        f"""s3://{bucket_name} ({len(bucket_objects)}):\n"""
        f"""{bucket_objects_text}\n"""
        f"""DyanmoDb Tables ({len(dynamodb_tablenames)}):\n"""
        f"""{dynamodb_tablenames_text}\n"""
        f"""CloudFormation{stack_name} ({len(stack_resources)}):\n"""
        f"""{stack_resources_text}\n"""
        """==============\n"""
        """The above resources will be destroyed!\n"""
    )
    do_destroy = input(f"To proceed with *destroy*, type, '{stack_name}':")
    if do_destroy == stack_name:
        destroy_bucket_objects(bucket_name, bucket_objects)
        destroy_bucket(bucket_name)
        destroy_dynamodb_tables(dynamodb_tablenames)
        destroy_stack(stack_name)
    else:
        logger.info("Aborted!")


def _handle_package_command(args: Namespace, user_runtime: ValidPythonRuntimes) -> None:
    parameters = SandjigSettingParameters(
        AppPath=args.appname, AwsRegion=AWS_DEFAULT_REGION, AwsProfile=AWS_PROFILE, Runtime=user_runtime
    )
    create_settings_file(parameters, stage=args.stage)
    application_filepath = create_deployment_package(stage=args.stage, output_directory=args.output_directory)
    logger.info(f"application package created: {application_filepath}")
    authorizer_filepath = create_authorizer_package(output_directory=args.output_directory)
    logger.info(f"authorizer package created: {authorizer_filepath}")


def process_commandline_args() -> None:
    """Preform command line argument processing, and perform the resulting user defined action(s)"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-p", "--prefix", default="sandjig-", help="prefix to apply to created resource names")
    parser.add_argument(
        "-s", "--suffix", required=True, default=SANDJIG_SUFFIX, help="unique suffix to apply to created resource names"
    )
    subparsers = parser.add_subparsers(dest="command")

    deploy_command_parser = subparsers.add_parser("deploy")
    deploy_command_parser.add_argument("-b", "--bucket", type=str, help="Source S3 Bucket Name (will be created)")
    deploy_command_parser.add_argument(
        "--stage", type=str, default=DEFAULT_APP_STAGE, help=f"stage name (stg|dev|prd) [DEFAULT={DEFAULT_APP_STAGE}]"
    )
    deploy_command_parser.add_argument(
        "-n", "--appname", required=True, type=filepath, help="full filepath to your application python file"
    )

    deploy_command_parser = subparsers.add_parser("update")
    deploy_command_parser.add_argument("-b", "--bucket", type=str, help="Source S3 Bucket Name (will be created)")
    deploy_command_parser.add_argument(
        "--stage", type=str, default=DEFAULT_APP_STAGE, help=f"stage name (stg|dev|prd) [DEFAULT={DEFAULT_APP_STAGE}]"
    )
    deploy_command_parser.add_argument(
        "-n", "--appname", required=True, type=filepath, help="full filepath to your application python file"
    )

    subparsers.add_parser("destroy")

    package_command_parser = subparsers.add_parser("package")
    package_command_parser.add_argument(
        "--stage", type=str, default=DEFAULT_APP_STAGE, help=f"stage name (stg|dev|prd) [DEFAULT={DEFAULT_APP_STAGE}]"
    )
    package_command_parser.add_argument(
        "-n", "--appname", required=True, type=filepath, help="full filepath to your application python file"
    )
    package_command_parser.add_argument(
        "-o",
        "--output-directory",
        dest="output_directory",
        required=True,
        type=directory_path,
        help="full filepath to your zappa package's zip file",
    )

    template_command_parser = subparsers.add_parser("template")
    template_command_parser.add_argument(
        "-o",
        "--output",
        type=yaml_filepath,
        default=DEFAULT_TEMPLATE_OUTPUT_FILEPATH,
        help="full filepath of cloudformation template file",
    )

    version_info = sys.version_info
    user_runtime = ValidPythonRuntimes(f"python{version_info.major}.{version_info.minor}")

    args = parser.parse_args()
    if args.command == "deploy":
        _handle_deploy_command(args, user_runtime)
    elif args.command == "update":
        _handle_update_command(args, user_runtime)
    elif args.command == "destroy":
        _handle_destroy_command(args)
    elif args.command == "package":
        _handle_package_command(args, user_runtime)
    elif args.command == "template":
        copy_cfn_template_file(TEMPLATE_FILEPATH, args.output)


if __name__ == "__main__":
    logger.info(f"sandjig {JOBSAPI_VERSION}")
    process_commandline_args()
