import tempfile
import unittest
from pathlib import Path

import pytest

from sandjig.definitions import SandjigAppStackParameters, SandjigSettingParameters, StageValues, ValidPythonRuntimes


class SandjigSettingParametersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.expected_appname = "myapp"
        self.python_file_contents = f"""from sandjig import create_app\n\n{self.expected_appname} = create_app()"""

    def test_get_app_function_in_package(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            package_name = "mypackage"
            package_dir = root_dir / package_name
            package_dir.mkdir(parents=True, exist_ok=True)
            package_file = package_dir / "__init__.py"
            package_file.write_text("", encoding="utf8")
            assert package_file.exists()
            module_name = "api"
            api_file = package_dir / f"{module_name}.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")

            parameters = SandjigSettingParameters(
                AppPath=api_file,
                AwsRegion="ap-northeast-1",
                AwsProfile="default",
                Runtime=ValidPythonRuntimes.PYTHON_314,
            )
            expected = f"{package_name}.{module_name}.{self.expected_appname}"
            actual = parameters.get_app_function()
            self.assertEqual(actual, expected)

    def test_get_app_function_singlefile(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")
            parameters = SandjigSettingParameters(
                AppPath=api_file,
                AwsRegion="ap-northeast-1",
                AwsProfile="default",
                Runtime=ValidPythonRuntimes.PYTHON_314,
            )
            expected = f"api.{self.expected_appname}"
            actual = parameters.get_app_function()
            self.assertEqual(actual, expected)

    def test_get_app_function_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text("invalid contents, no app defined", encoding="utf8")
            parameters = SandjigSettingParameters(
                AppPath=api_file,
                AwsRegion="ap-northeast-1",
                AwsProfile="default",
                Runtime=ValidPythonRuntimes.PYTHON_314,
            )
            with pytest.raises(ValueError):
                parameters.get_app_function()


class SandjigAppStackParametersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.expected_appname = "myapp"
        self.python_file_contents = f"""from sandjig import create_app\n\n{self.expected_appname} = create_app()"""

    def test_suffix_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")
            suffix = "-witdash"
            parameters = SandjigAppStackParameters(
                SandjigFunctionZipPackageLocalPath=api_file,
                UniqueSuffix=suffix,
            )
            expected = suffix.replace("-", "")
            self.assertEqual(parameters.UniqueSuffix, expected)

    def test_prefix_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")
            prefix = "withdash-"
            suffix = "-witdash"
            parameters = SandjigAppStackParameters(
                SandjigFunctionZipPackageLocalPath=api_file,
                Prefix=prefix,
                UniqueSuffix=suffix,
            )
            expected = prefix.replace("-", "")
            self.assertEqual(parameters.Prefix, expected)

    def test_get_stack_name(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")
            prefix = "withdash-"
            suffix = "-witdash"
            parameters = SandjigAppStackParameters(
                SandjigFunctionZipPackageLocalPath=api_file,
                Prefix=prefix,
                UniqueSuffix=suffix,
            )
            default_stage = StageValues.DEVELOPMENT.value
            expected = f"{prefix}sdj{suffix}-{default_stage}"
            self.assertEqual(parameters.get_stack_name(), expected)

    def test_validate_sources3bucketname__default(self):
        with tempfile.TemporaryDirectory() as d:
            root_dir = Path(d)
            api_file = root_dir / "api.py"
            api_file.write_text(self.python_file_contents, encoding="utf8")
            prefix = "withdash-"
            suffix = "-witdash"
            parameters = SandjigAppStackParameters(
                SandjigFunctionZipPackageLocalPath=api_file,
                Prefix=prefix,
                UniqueSuffix=suffix,
            )
            expected_default_bucket_name = (
                f"{parameters.Prefix}-sdsrc-{parameters.UniqueSuffix}-{parameters.APIGatewayStage.value}"
            )
            self.assertEqual(parameters.SourceS3BucketName, expected_default_bucket_name)
