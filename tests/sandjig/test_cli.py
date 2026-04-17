import logging
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase, mock

from sandjig.cli import SandjigAppStackParameters, create_authorizer_package, deploy
from sandjig.definitions import StageValues

from ..mocks import CloudformationWaiterMock
from ..utils import get_zappa_zip_package

logging.basicConfig(
    stream=sys.stdout, level=logging.DEBUG, format="%(asctime)s [%(levelname)s] (%(name)s) %(funcName)s: %(message)s"
)
logger = logging.getLogger(__name__)


class CliTestCase(TestCase):
    @mock.patch("sandjig.aws.CFN_CLIENT.get_waiter", return_value=CloudformationWaiterMock())
    @mock.patch("sandjig.aws.CFN_CLIENT.create_stack", return_value={"ResponseMetadata": {"HTTPStatusCode": 200}})
    @mock.patch(
        "sandjig.aws.CFN_CLIENT.describe_stacks",
        return_value={
            "Stacks": [
                {"Outputs": [{"OutputKey": "AWSApiGatewayWithBasicAuthUrl", "OutputValue": "http://test.com/dev"}]}
            ]
        },
    )
    def test_deploy(self, *_, **__):
        prefix = "test-"
        suffix = "-jakfl90"
        stage = StageValues.DEVELOPMENT.value
        zappa_zip_package = get_zappa_zip_package()
        with tempfile.TemporaryDirectory() as d:
            authorizer_filepath = create_authorizer_package(Path(d))
            parameters = SandjigAppStackParameters(
                Prefix=prefix,
                UniqueSuffix=suffix,
                APIGatewayStage=stage,
                SandjigFunctionZipPackageLocalPath=zappa_zip_package,
                SandjigAuthorizerZipPackageLocalPath=authorizer_filepath,
            )
            response = deploy(parameters)
            self.assertTrue(response)

    def test_create_authorizer_package(self):
        with tempfile.TemporaryDirectory() as d:
            result_filepath = create_authorizer_package(Path(d))
            self.assertTrue(result_filepath.exists())

            # unzip and check contents
            z = zipfile.ZipFile(str(result_filepath), "r")
            expected = ("apigwauthorizers/__init__.py", "apigwauthorizers/basicauth.py", "apigwauthorizers/")
            for zipinfo in z.filelist:
                if "__pycache__" not in zipinfo.filename:  # ignore __pycache__ files
                    self.assertIn(zipinfo.filename, expected)
