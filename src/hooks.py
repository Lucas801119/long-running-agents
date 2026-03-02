"""SDK 安全钩子：保护功能列表完整性、阻止危险 Git 操作、记录工具调用。

核心理念来自文章：Agent 只应该修改 features.json 的 passes 字段，
不应执行 git push 等危险操作。通过 PreToolUse 钩子在工具执行前拦截。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("orchestrator.hooks")

# 危险的 git 命令模式
DANGEROUS_GIT_PATTERNS = [
    r"git\s+push",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-f",
    r"git\s+checkout\s+\.",
    r"git\s+restore\s+\.",
    r"git\s+branch\s+-D",
    r"git\s+rebase",
    r"rm\s+-rf",
]


def make_feature_list_guard(project_dir: str) -> Any:
    """创建功能列表保护钩子。

    拦截对 features.json 的写入操作，验证只有 passes 字段被修改。
    如果 Agent 试图修改功能结构（删除/添加/修改描述），拒绝写入。
    """

    async def guard(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # 只检查写入 features.json 的操作
        file_path = tool_input.get("file_path", "")
        if not file_path.endswith("features.json"):
            return {}

        if tool_name == "Write":
            # 对于完整写入，验证内容
            content = tool_input.get("content", "")
            return await _validate_features_write(project_dir, content)
        elif tool_name == "Edit":
            # 对于编辑操作，检查 old_string 和 new_string
            old_str = tool_input.get("old_string", "")
            new_str = tool_input.get("new_string", "")
            return _validate_features_edit(old_str, new_str)

        return {}

    return guard


async def _validate_features_write(project_dir: str, content: str) -> dict:
    """验证对 features.json 的完整写入。"""
    features_path = Path(project_dir) / "features.json"

    # 如果文件不存在（首次创建），允许写入
    if not features_path.exists():
        return {}

    try:
        new_data = json.loads(content)
    except json.JSONDecodeError:
        return _deny("features.json 的内容不是有效的 JSON")

    try:
        with open(features_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

    # 检查数量是否一致
    if len(new_data) != len(old_data):
        return _deny(
            f"不允许添加或删除功能。当前 {len(old_data)} 个，"
            f"尝试写入 {len(new_data)} 个。"
        )

    # 逐项检查，只允许 passes 字段变更
    old_map = {item["id"]: item for item in old_data}
    for new_item in new_data:
        fid = new_item.get("id")
        if fid not in old_map:
            return _deny(f"发现未知功能 ID: {fid}")

        old_item = old_map[fid]
        for key in ["category", "description", "steps", "priority"]:
            if new_item.get(key) != old_item.get(key):
                return _deny(
                    f"不允许修改功能 #{fid} 的 {key} 字段。"
                    f"只能修改 passes 字段。"
                )

    # 验证通过，允许写入
    logger.info("features.json 写入验证通过")
    return {}


def _validate_features_edit(old_str: str, new_str: str) -> dict:
    """验证对 features.json 的编辑操作。

    允许的模式：将 "passes": false 改为 "passes": true（或反向）。
    """
    # 允许 passes 字段的变更
    passes_pattern = r'"passes"\s*:\s*(true|false)'
    old_passes = re.findall(passes_pattern, old_str)
    new_passes = re.findall(passes_pattern, new_str)

    # 移除 passes 相关内容后比较
    old_cleaned = re.sub(passes_pattern, "", old_str).strip()
    new_cleaned = re.sub(passes_pattern, "", new_str).strip()

    if old_cleaned != new_cleaned:
        # 还有除 passes 外的其他变更
        return _deny(
            "features.json 编辑中包含 passes 以外的变更。"
            "只允许修改 passes 字段。"
        )

    return {}


def make_git_safety_hook() -> Any:
    """创建 Git 安全钩子。

    阻止危险的 git 命令（push, reset --hard, clean -f 等）。
    """

    async def guard(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        if tool_name != "Bash":
            return {}

        command = input_data.get("tool_input", {}).get("command", "")

        for pattern in DANGEROUS_GIT_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return _deny(
                    f"阻止执行危险命令：{command}\n"
                    f"匹配的危险模式：{pattern}"
                )

        return {}

    return guard


def make_logging_hook(verbose: bool = False) -> Any:
    """创建工具调用日志钩子。"""

    async def log_hook(
        input_data: dict, tool_use_id: str, context: Any
    ) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        if verbose:
            logger.info(f"[工具调用] {tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:200]}")
        else:
            # 简洁模式只记录工具名
            logger.debug(f"[工具调用] {tool_name}")

        return {}

    return log_hook


def _deny(reason: str) -> dict:
    """构建拒绝响应。"""
    logger.warning(f"[钩子拒绝] {reason}")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
