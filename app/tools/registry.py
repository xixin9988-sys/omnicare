import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from app.governance.ports import GovernancePort
from app.models.entities import GovernanceIncident


class Permission(StrEnum):
    READ_ONLY = "read_only"
    BUSINESS_WRITE = "business_write"
    EXTERNAL = "external"


@dataclass(slots=True)
class ToolContext:
    user_id: str
    session_id: str
    run_id: str
    db: Any
    governance: GovernancePort
    emit_event: Callable[[str, str, str, dict | None], Awaitable[None]] | None = None


@dataclass(slots=True)
class ToolResult:
    success: bool
    data: dict[str, Any]
    error_code: str | None = None
    message: str | None = None


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]
    permission: Permission
    timeout_seconds: float
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        if spec.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._tools[spec.name] = spec

    async def execute(
        self, name: str, raw_args: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, {}, "tool_not_registered", "工具未注册。")
        try:
            args = spec.args_schema.model_validate(raw_args)
        except Exception:
            return ToolResult(False, {}, "tool_args_invalid", "工具参数未通过数据校验。")
        if spec.permission == Permission.BUSINESS_WRITE:
            decision = await context.governance.authorize_action(
                action=name,
                args=args.model_dump(mode="json"),
                context={"user_id": context.user_id, "session_id": context.session_id},
                db=context.db,
            )
            if not decision.allowed:
                context.db.add(
                    GovernanceIncident(
                        run_id=context.run_id,
                        user_id=context.user_id,
                        category="action",
                        decision=decision.decision,
                        reason_code=decision.reason_code,
                        detail=decision.detail[:500],
                    )
                )
                if context.emit_event:
                    await context.emit_event(
                        "tool_denied", name, "blocked", {"reason_code": decision.reason_code}
                    )
                return ToolResult(
                    False,
                    {
                        "governance_decision": decision.decision,
                        "reason_code": decision.reason_code,
                        "detail": decision.detail,
                    },
                    decision.reason_code,
                    "操作被安全治理策略拦截。",
                )
        if context.emit_event:
            await context.emit_event(
                "tool_started", name, "running", {"permission": spec.permission.value}
            )
        try:
            result = await asyncio.wait_for(
                spec.handler(args, context), timeout=spec.timeout_seconds
            )
            if context.emit_event:
                await context.emit_event(
                    "tool_completed",
                    name,
                    "completed" if result.success else "failed",
                    {"success": result.success, "error_code": result.error_code},
                )
            return result
        except TimeoutError:
            if context.emit_event:
                await context.emit_event(
                    "tool_failed", name, "failed", {"error_code": "tool_timeout"}
                )
            return ToolResult(False, {}, "tool_timeout", "业务工具执行超时。")
        except Exception:
            # Never return stack traces, credentials or internal paths to the model/user.
            if context.emit_event:
                await context.emit_event(
                    "tool_failed", name, "failed", {"error_code": "tool_internal_error"}
                )
            return ToolResult(False, {}, "tool_internal_error", "业务工具执行失败，请稍后重试。")

    def public_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "args_schema": spec.args_schema.model_json_schema(),
                "permission": spec.permission.value,
                "timeout_seconds": spec.timeout_seconds,
            }
            for spec in self._tools.values()
        ]
