"""初始化 Agent：项目首次设置。

职责：
1. 创建项目脚手架（根据技术栈）
2. 生成 features.json（完整功能清单）
3. 创建 init.sh（环境启动脚本）
4. 创建 claude-progress.txt（进度跟踪文件）
5. 初始化 git 仓库并做首次提交
"""

from __future__ import annotations

import logging
from pathlib import Path

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
from ..hooks import make_git_safety_hook, make_logging_hook
from ..prompts import build_initializer_system_prompt, build_initializer_user_prompt

logger = logging.getLogger("orchestrator.initializer")


def build_options(config: RuntimeConfig) -> ClaudeAgentOptions:
    """构建初始化 Agent 的 SDK 选项。"""
    hooks_list = [
        make_git_safety_hook(),
        make_logging_hook(verbose=config.verbose),
    ]

    return ClaudeAgentOptions(
        system_prompt=None,  # 会在 run() 中通过 prompt 组合传入
        allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
        permission_mode=config.permission_mode,
        cwd=config.project_dir,
        model=config.model,
        max_turns=config.max_turns_per_session,
        hooks={
            "PreToolUse": [HookMatcher(hooks=hooks_list)],
        },
    )


async def run_initializer(
    spec: ProjectSpec, config: RuntimeConfig
) -> SessionResult:
    """执行初始化 Agent 会话。"""
    logger.info(f"开始初始化项目: {spec.name}")

    # 确保项目目录存在
    project_path = Path(config.project_dir)
    project_path.mkdir(parents=True, exist_ok=True)

    # 构建提示词
    system_prompt = build_initializer_system_prompt(spec)
    user_prompt = build_initializer_user_prompt(spec)

    # 组合为完整提示
    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    options = build_options(config)
    options.system_prompt = system_prompt

    session_id = None
    result_text = ""
    success = False

    try:
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, SystemMessage):
                if hasattr(message, "data") and message.data:
                    session_id = message.data.get("session_id")
                    logger.info(f"初始化会话已启动: {session_id}")

            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        logger.info(f"[初始化] {block.text[:200]}")
                        result_text += block.text + "\n"
                    elif isinstance(block, ToolUseBlock):
                        logger.debug(f"[工具] {block.name}")

            elif isinstance(message, ResultMessage):
                success = message.subtype == "success"
                if message.result:
                    result_text += str(message.result)
                logger.info(
                    f"初始化完成: {'成功' if success else '失败'}"
                )

    except Exception as e:
        logger.error(f"初始化 Agent 异常: {e}")
        return SessionResult(
            session_id=session_id,
            success=False,
            summary=f"初始化失败: {e}",
            error=str(e),
        )

    # 如果 Agent 没有创建 claude-progress.txt，由编排器补创建
    progress_path = Path(config.project_dir) / "claude-progress.txt"
    if not progress_path.exists():
        from ..progress import init_progress_file
        init_progress_file(config.project_dir, spec.name)
        logger.info("已补创建 claude-progress.txt")

    # 验证关键文件是否已创建
    checks = _verify_initialization(config.project_dir)
    if not all(checks.values()):
        missing = [k for k, v in checks.items() if not v]
        logger.warning(f"初始化不完整，缺少: {missing}")
        return SessionResult(
            session_id=session_id,
            success=False,
            summary=f"初始化不完整，缺少文件: {', '.join(missing)}",
        )

    return SessionResult(
        session_id=session_id,
        success=success,
        summary="项目初始化完成：脚手架、features.json、init.sh、git 仓库已创建",
    )


def _verify_initialization(project_dir: str) -> dict[str, bool]:
    """验证初始化是否完成（关键文件是否存在）。"""
    project_path = Path(project_dir)
    return {
        "features.json": (project_path / "features.json").exists(),
        "init.sh": (project_path / "init.sh").exists(),
        "claude-progress.txt": (project_path / "claude-progress.txt").exists(),
        ".git": (project_path / ".git").exists(),
    }


def is_initialized(project_dir: str) -> bool:
    """检查项目是否已初始化。"""
    checks = _verify_initialization(project_dir)
    return all(checks.values())
