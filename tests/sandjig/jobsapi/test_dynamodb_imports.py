import importlib
import sys
import warnings
from unittest import TestCase

from sandjig.jobsapi.dynamodb.models import ProcessingJobModel


class DynamoDbImportTestCase(TestCase):
    def test_dynamodb_models_import_uses_correct_module_name(self):
        module = importlib.import_module("sandjig.jobsapi.dynamodb.models")

        self.assertIs(module.ProcessingJobModel, ProcessingJobModel)

    def test_dyanmodb_models_import_warns_and_reexports_models(self):
        sys.modules.pop("sandjig.jobsapi.dyanmodb.models", None)
        sys.modules.pop("sandjig.jobsapi.dyanmodb", None)

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", DeprecationWarning)
            module = importlib.import_module("sandjig.jobsapi.dyanmodb.models")

        self.assertIs(module.ProcessingJobModel, ProcessingJobModel)
        self.assertTrue(
            any(
                "sandjig.jobsapi.dyanmodb.models is deprecated" in str(warning.message)
                for warning in captured
            ),
        )
