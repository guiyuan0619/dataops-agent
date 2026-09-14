from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field

ALLOWED_TOOL_NAMES = {
    "load_dataset",
    "profile_dataset",
    "detect_issues",
    "build_repair_plan",
    "preview_repair",
    "apply_repair",
    "publish_version",
}


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


def validate_tool_calls(calls: Iterable[ToolCall | dict[str, Any]]) -> list[ToolCall]:
    validated: list[ToolCall] = []
    for call in calls:
        item = call if isinstance(call, ToolCall) else ToolCall.model_validate(call)
        if item.name not in ALLOWED_TOOL_NAMES:
            raise ValueError(f"Tool is not allowed: {item.name}")
        validated.append(item)
    return validated


def offline_governance_tool_calls(*, include_write: bool) -> list[ToolCall]:
    names = [
        "load_dataset",
        "profile_dataset",
        "detect_issues",
        "build_repair_plan",
        "preview_repair",
    ]
    if include_write:
        names.extend(["apply_repair", "publish_version"])
    return validate_tool_calls(ToolCall(name=name) for name in names)
