"""Tests for lazy settings resolution (#21)."""

import os
import subprocess
import sys

import pytest

CREDENTIAL_ENV_KEYS = (
    "AWS_ACCOUNT_ID",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_PROFILE",
    "STS_SERVICE_ENDPOINT",
)


def test_import_does_not_require_aws_credentials():
    """Importing sandjig must not call STS (#21) — run in a credential-less subprocess."""
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV_KEYS}
    # Point the SDK at an empty config so ambient credentials/config files can't leak in.
    env["AWS_CONFIG_FILE"] = "/dev/null"
    env["AWS_SHARED_CREDENTIALS_FILE"] = "/dev/null"
    result = subprocess.run(
        [sys.executable, "-c", "import sandjig; import sandjig.settings; print('import-ok')"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "import-ok" in result.stdout


def test_env_account_id_used_without_sts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    from sandjig import settings

    monkeypatch.setattr(settings, "_aws_account_id_cache", "123456789012")
    assert settings.AWS_ACCOUNT_ID == "123456789012"
    assert settings.get_aws_account_id() == "123456789012"


def test_unknown_settings_attribute_raises():
    from sandjig import settings

    try:
        _ = settings.DOES_NOT_EXIST
        raise AssertionError("expected AttributeError")
    except AttributeError:
        pass
