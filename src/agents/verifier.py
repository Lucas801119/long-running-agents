"""验证 Agent：端到端功能验证。

在所有功能完成（或达到最大会话数）后运行。
逐一验证标记为 passes=true 的功能，将失败的改回 false。
"""

from __future__ import annotations

import logging

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from ..config import ProjectSpec, RuntimeConfig, SessionResult
from ..features import load_features, get_progress_summary
from ..hooks import make_logging_hook
from ..prompts import build_verifier_system_prompt, build_verifier_user_prompt

logger = logging.getLogger("orchestrator.verifier")


def build_options(
    config: RuntimeConfig, spec: ProjectSpec
) -> ClaudeAgentOptions:
    """构建验证 Agent 的 SDK 选项。"""
    features = load_features(config.project_dir)
    features_summary = _build_features_summary(features)
    system_prompt = build_verifier_system_prompt(spec, features_summary)

    hooks_list = [make_logging_hook(verbose=config.verbose)]

    mcp_servers = {}
    allowed_tools = ["Read", "Bash", "Glob", "Grep"]

    if config.use_playwright:
        mcp_servers["playwright"] = {
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        }
        allowed_tools.extend([
            "mcp__playwright__navigate",
            "mcp__playwright__screenshot",
            "mcp__playwright__click",
            "mcp__playwright__fill",
            "mcp__playwright__evaluate",
        ])

    opts = ClaudeAgentOptions(
        system_prompt=system_prompt,
        # 验证 Agent 可以修改 features.json（将失败的改回 false）
        # 但也可以读和编辑
        allowed_tools=allowed_tools + ["Edit"],
        permission_mode=config.permission_mode,
        cwd=config.project_dir,
        model=config.model,
        max_turns=config.max_turns_per_session,
        hooks={
            "PreToolUse": [HookMatcher(hooks=hooks_list)],
        },
    )

    if mcp_servers:
        opts.mcp_servers = mcp_servers

    return opts


async def run_verifier(
    spec: ProjectSpec, config: RuntimeConfig
) -> SessionResult:
    """执行验证 Agent 会话。"""
    features = load_features(config.project_dir)
    passed_count = sum(1 for f in features if f.passes)

    if passed_count == 0:
        logger.info("没有需要验证的功能。")
        return SessionResult(
            success=True,
            summary="没有标记为通过的功能需要验证。",
        )

    logger.info(f"开始验证 {passed_count} 个已通过的功能...")

    user_prompt = build_verifier_user_prompt()
    options = build_options(config, spec)

    session_id = None
    result_text = ""
    success = False

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, SystemMessage):
                if hasattr(message, "data") and message.data:
                    session_id = message.data.get("session_id")

            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        logger.info(f"[验证] {block.text[:200]}")
                        result_text += block.text + "\n"
                    elif isinstance(block, ToolUseBlock):
                        logger.debug(f"[工具] {block.name}")

            elif isinstance(message, ResultMessage):
                success = message.subtype == "success"
                if message.result:
                    result_text += str(message.result)

    except Exception as e:
        logger.error(f"验证 Agent 异常: {e}")
        return SessionResult(
            session_id=session_id,
            success=False,
            summary=f"验证会话异常: {e}",
            error=str(e),
        )

    # 重新加载功能列表查看验证后的状态
    features_after = load_features(config.project_dir)
    passed_after = sum(1 for f in features_after if f.passes)
    reverted = passed_count - passed_after

    summary = (
        f"验证完成。{passed_after}/{len(features_after)} 功能通过验证。"
    )
    if reverted > 0:
        summary += f" {reverted} 个功能被重新标记为未通过。"

    return SessionResult(
        session_id=session_id,
        success=success,
        summary=summary,
    )


def _build_features_summary(features: list) -> str:
    """构建功能状态摘要供验证 Agent 参考。"""
    lines = [get_progress_summary(features), "", "已标记为通过的功能："]

    for feat in features:
        if feat.passes:
            steps_str = " → ".join(feat.steps)
            lines.append(
                f"  #{feat.id} [{feat.category}] {feat.description}"
                f"\n    验证步骤: {steps_str}"
            )

    return "\n".join(lines)
