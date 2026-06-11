"""Tests for the `sandjig template` command sources, including --resources-only."""

import tempfile
from pathlib import Path
from unittest import TestCase

from sandjig.cli import copy_cfn_template_file
from sandjig.settings import RESOURCES_TEMPLATE_FILEPATH, TEMPLATE_FILEPATH


class ResourcesTemplateTestCase(TestCase):
    def test_resources_template_exists(self):
        self.assertTrue(RESOURCES_TEMPLATE_FILEPATH.exists())

    def test_resources_template_contains_data_plane_resources(self):
        content = RESOURCES_TEMPLATE_FILEPATH.read_text(encoding="utf8")
        # data plane present
        self.assertIn("AWS::DynamoDB::Table", content)
        self.assertIn("AWS::SQS::Queue", content)
        # key schema matches the PynamoDB models
        self.assertIn("job_id", content)
        self.assertIn("yyyymm", content)
        self.assertIn("sort_key", content)
        self.assertIn("settings_id", content)
        # zero-idle billing
        self.assertIn("PAY_PER_REQUEST", content)
        # compute plane absent — host application provides it
        self.assertNotIn("AWS::Serverless::Function", content)
        self.assertNotIn("AWS::Serverless::Api", content)

    def test_resources_template_outputs_for_host_wiring(self):
        content = RESOURCES_TEMPLATE_FILEPATH.read_text(encoding="utf8")
        for output in (
            "ProcessingJobSQSQueueUrl",
            "ProcessingJobRequestsTableName",
            "ProcessingSettingsTableName",
        ):
            self.assertIn(output, content)

    def test_copy_resources_template(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "resources.yaml"
            copy_cfn_template_file(RESOURCES_TEMPLATE_FILEPATH, output)
            self.assertTrue(output.exists())
            self.assertEqual(output.read_text(encoding="utf8"), RESOURCES_TEMPLATE_FILEPATH.read_text(encoding="utf8"))

    def test_app_template_unchanged_default(self):
        # the full-stack template remains the default `sandjig template` source
        content = TEMPLATE_FILEPATH.read_text(encoding="utf8")
        self.assertIn("AWS::Serverless::Function", content)
