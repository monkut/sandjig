import importlib
from unittest import TestCase

import pytest

from sandjig.jobsapi.dynamodb.models import ProcessingJobModel


class DynamoDbImportTestCase(TestCase):
    def test_dynamodb_models_import_uses_correct_module_name(self):
        module = importlib.import_module("sandjig.jobsapi.dynamodb.models")

        self.assertIs(module.ProcessingJobModel, ProcessingJobModel)

    def test_typo_dyanmodb_module_removed(self):
        """The typo path is removed (hard rename, no compatibility shim)."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("sandjig.jobsapi.dyanmodb.models")
