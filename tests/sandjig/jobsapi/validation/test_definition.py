import pytest
from pydantic import ValidationError

from sandjig.jobsapi.validation.definitions import ProcessingJobPatchBody, StatusSupportedValues


def test_processingjobpatchbody_validation__valid():
    data = {
        'status': StatusSupportedValues.CANCELLED.value
    }
    validated_data = ProcessingJobPatchBody(**data)
    assert validated_data


def test_processingjobpatchbody_validation__invalid():
    data = {
        'status': "other"
    }
    with pytest.raises(ValidationError):
        _ = ProcessingJobPatchBody(**data)
