"""编码 Agent：逐个功能实现。

每个会话专注于实现一个功能，遵循严格协议：
1. 定位 - 确认环境，阅读进度和功能列表
2. 实现 - 编写代码，只做当前功能
3. 验证 - 测试功能是否正常
4. 提交 - git commit，更新 features.json 和 progress
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

from ..config import Feature, ProjectSpec, RuntimeConfig, SessionResult
from ..features import load_features, get_feature_by_id
from ..hooks import (
    make_feature_list_guard,
    make_git_safety_hook,
    make_logging_hook,
)
from ..progress import read_progress
from ..prompts import build_coder_system_prompt, build_coder_user_prompt

logger = logging.getLogger("orchestrator.coder")


def build_options(
    config: RuntimeConfig, feature: Feature, spec: ProjectSpec
) -> ClaudeAgentOptions:
    """构建编码 Agent 的 SDK 选项。"""
    progress_content = read_progress(config.project_dir)
    system_prompt = build_coder_system_prompt(spec, feature, progress_content)

    hooks_list = [
        make_feature_list_guard(config.project_dir),
        make_git_safety_hook(),
        make_logging_hook(verbose=config.verbose),
    ]

    mcp_servers = {}
    if config.use_playwright:
        mcp_servers["playwright"] = {
            "command": "npx",
            "args": ["@playwright/mcp@latest"],
        }

    opts = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
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
        # 添加 Playwright 工具到允许列表
        opts.allowed_tools.extend([
            "mcp__playwright__navigate",
            "mcp__playwright__screenshot",
            "mcp__playwright__click",
            "mcp__playwright__fill",
            "mcp__playwright__evaluate",
        ])

    return opts


async def run_coder(
    feature: Feature,
    spec: ProjectSpec,
    config: RuntimeConfig,
    session_num: int,
) -> SessionResult:
    """执行编码 Agent 会话，实现指定功能。"""
    logger.info(
        f"会话 {session_num}: 开始实现功能 #{feature.id} - {feature.description}"
    )

    user_prompt = build_coder_user_prompt(feature)
    options = build_options(config, feature, spec)

    session_id = None
    result_text = ""
    success = False
    tool_calls: list[str] = []

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, SystemMessage):
                if hasattr(message, "data") and message.data:
                    session_id = message.data.get("session_id")
                    logger.debug(f"编码会话已启动: {session_id}")

            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        logger.info(f"[编码] {block.text[:150]}")
                        result_text += block.text + "\n"
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(block.name)
                        logger.debug(f"[工具] {block.name}")

            elif isinstance(message, ResultMessage):
                success = message.subtype == "success"
                if message.result:
                    result_text += str(message.result)
                logger.info(
                    f"编码会话完成: {'成功' if success else '失败'}"
                )

    except Exception as e:
        logger.error(f"编码 Agent 异常: {e}")
        return SessionResult(
            session_id=session_id,
            success=False,
            feature_id=feature.id,
            feature_passed=False,
            summary=f"编码会话异常: {e}",
            error=str(e),
        )

    # 检查功能是否实际被标记为通过
    feature_passed = _check_feature_passed(config.project_dir, feature.id)

    return SessionResult(
        session_id=session_id,
        success=success,
        feature_id=feature.id,
        feature_passed=feature_passed,
        summary=(
            f"功能 #{feature.id} ({feature.description}): "
            f"{'已通过' if feature_passed else '未通过'}。"
            f"使用了 {len(tool_calls)} 次工具调用。"
        ),
    )


def _check_feature_passed(project_dir: str, feature_id: int) -> bool:
    """检查指定功能在 features.json 中是否被标记为通过。"""
    features = load_features(project_dir)
    feat = get_feature_by_id(features, feature_id)
    return feat.passes if feat else False
