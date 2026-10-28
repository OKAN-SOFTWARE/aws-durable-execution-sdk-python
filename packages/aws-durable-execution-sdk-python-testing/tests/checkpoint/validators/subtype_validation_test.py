"""Regression coverage for CheckpointDurableExecution SubType validation (#836)."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from aws_durable_execution_sdk_python.lambda_service import (
    OperationAction,
    OperationType,
    OperationUpdate,
)

from aws_durable_execution_sdk_python_testing.checkpoint.core import CheckpointCore
from aws_durable_execution_sdk_python_testing.checkpoint.validators.checkpoint import (
    CheckpointValidator,
)
from aws_durable_execution_sdk_python_testing.exceptions import AwsApiException
from aws_durable_execution_sdk_python_testing.execution import Execution
from aws_durable_execution_sdk_python_testing.model import StartDurableExecutionInput
from aws_durable_execution_sdk_python_testing.web.models import HTTPResponse


PATTERN = "[a-zA-Z0-9-_]+"


def _execution() -> Execution:
    execution = Execution.new(
        StartDurableExecutionInput(
            account_id="123456789012",
            function_name="test-function",
            function_qualifier="$LATEST",
            execution_name="subtype-validation-test",
            execution_timeout_seconds=900,
            execution_retention_period_days=7,
            input="{}",
            invocation_id="test-invocation-id",
        )
    )
    execution.start()
    return execution


def _update(sub_type: str | None, operation_id: str = "step-1") -> OperationUpdate:
    return OperationUpdate(
        operation_id=operation_id,
        operation_type=OperationType.STEP,
        action=OperationAction.START,
        sub_type=sub_type,
    )


@pytest.mark.parametrize("sub_type", ["a", "x" * 32, "Order_Charge-21"])
def test_accepted_subtypes_at_boundaries(sub_type: str) -> None:
    CheckpointValidator.validate_input([_update(sub_type)], _execution())


def test_missing_optional_subtype_is_allowed() -> None:
    CheckpointValidator.validate_input([_update(None)], _execution())


@pytest.mark.parametrize(
    ("sub_type", "constraints"),
    [
        pytest.param(
            "",
            (
                "Member must have length greater than or equal to 1",
                f"Member must satisfy regular expression pattern: {PATTERN}",
            ),
            id="empty-has-two-errors",
        ),
        pytest.param(
            "x" * 33,
            ("Member must have length less than or equal to 32",),
            id="over-max-length",
        ),
        pytest.param(
            "order charge",
            (f"Member must satisfy regular expression pattern: {PATTERN}",),
            id="spaces",
        ),
        pytest.param(
            "ücret",
            (f"Member must satisfy regular expression pattern: {PATTERN}",),
            id="non-ascii",
        ),
    ],
)
def test_invalid_subtype_is_rejected_with_service_errors(
    sub_type: str, constraints: tuple[str, ...]
) -> None:
    with pytest.raises(AwsApiException) as exc_info:
        CheckpointValidator.validate_input([_update(sub_type)], _execution())
    message = str(exc_info.value)
    expected_errors = [
        (
            f"Value '{sub_type}' at 'updates.1.member.subType' "
            f"failed to satisfy constraint: {constraint}"
        )
        for constraint in constraints
    ]
    noun = "error" if len(expected_errors) == 1 else "errors"
    expected = (
        f"{len(expected_errors)} validation {noun} detected: "
        + "; ".join(expected_errors)
    )
    assert message == expected
    response = HTTPResponse.create_error_from_exception(exc_info.value)
    assert response.status_code == 400
    assert response.body == {"__type": "ValidationException", "message": message}


def test_multiple_invalid_updates_report_in_input_order() -> None:
    oversized = "x" * 33
    updates = [
        _update("order charge", operation_id="step-1"),
        _update(oversized, operation_id="step-2"),
    ]
    with pytest.raises(AwsApiException) as exc_info:
        CheckpointValidator.validate_input(updates, _execution())
    message = str(exc_info.value)
    assert message == (
        "2 validation errors detected: "
        "Value 'order charge' at 'updates.1.member.subType' "
        "failed to satisfy constraint: "
        f"Member must satisfy regular expression pattern: {PATTERN}; "
        f"Value '{oversized}' at 'updates.2.member.subType' "
        "failed to satisfy constraint: "
        "Member must have length less than or equal to 32"
    )


def test_rejects_whole_checkpoint_without_mutating_execution() -> None:
    execution = _execution()
    dispatcher = Mock()
    operations_before = list(execution.operations)
    checkpoint_before = execution.last_checkpoint
    token_before = execution.token_sequence
    updates = [_update("valid"), _update("invalid subtype", "step-2")]
    with pytest.raises(AwsApiException):
        CheckpointCore.apply(
            execution=execution,
            checkpoint_token="original-token",
            updates=updates,
            client_token="client-1",
            dispatcher=dispatcher,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    dispatcher.apply_updates.assert_not_called()
    assert list(execution.operations) == operations_before
    assert execution.last_checkpoint == checkpoint_before
    assert execution.token_sequence == token_before


def test_model_validation_caps_reported_errors_at_ten() -> None:
    updates = [_update("x" * 33, operation_id=f"step-{index}") for index in range(12)]
    with pytest.raises(AwsApiException) as exc_info:
        CheckpointValidator.validate_input(updates, _execution())
    message = str(exc_info.value)
    assert message.startswith("10 validation errors detected:")
    assert "updates.10.member.subType" in message
    assert "updates.11.member.subType" not in message
