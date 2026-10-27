"""End-to-end parity: invalid SubType fails the durable execution (#836)."""

from typing import Any

import pytest
from aws_durable_execution_sdk_python.config import StepConfig
from aws_durable_execution_sdk_python.context import DurableContext, durable_step
from aws_durable_execution_sdk_python.execution import (
    InvocationStatus,
    durable_execution,
)
from aws_durable_execution_sdk_python.types import StepContext

from aws_durable_execution_sdk_python_testing.execution import ExecutionStatus
from aws_durable_execution_sdk_python_testing.runner import DurableFunctionTestRunner


@pytest.mark.parametrize(
    ("sub_type", "constraint"),
    [
        ("order charge", "Member must satisfy regular expression pattern"),
        ("x" * 33, "Member must have length less than or equal to 32"),
    ],
)
def test_invalid_step_subtype_fails_execution(sub_type: str, constraint: str) -> None:
    reached_after_step: list[bool] = []

    @durable_step
    def charge(step_context: StepContext) -> str:  # noqa: ARG001
        return "ok"

    # The SDK validates StepConfig at construction. Test-only mutation bypasses
    # that client guard to exercise the independent local service constraint.
    config = StepConfig(sub_type="ValidSubtype")
    object.__setattr__(config, "sub_type", sub_type)

    @durable_execution
    def handler(event: Any, context: DurableContext) -> str:  # noqa: ARG001
        context.step(charge(), config=config)
        reached_after_step.append(True)
        return "done"

    with DurableFunctionTestRunner(handler=handler, execution_timeout=10) as runner:
        result = runner.run(input="{}")

    assert result.status is InvocationStatus.FAILED
    assert result.execution_status is ExecutionStatus.FAILED
    assert result.error is not None
    assert result.error.message is not None
    assert ".member.subType" in result.error.message
    assert constraint in result.error.message
    assert not reached_after_step
