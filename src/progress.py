"""进度文件管理：claude-progress.txt 的读写。

每个会话结束后追加一条记录，包含会话号、处理的功能、状态和变更摘要。
下一个会话开始时读取此文件以了解之前的工作。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def read_progress(project_dir: str | Path) -> str:
    """读取 claude-progress.txt 的完整内容。"""
    progress_path = Path(project_dir) / "claude-progress.txt"
    if not progress_path.exists():
        return ""
    return progress_path.read_text(encoding="utf-8")


def append_progress(
    project_dir: str | Path,
    session_num: int,
    feature_description: str,
    status: str,
    summary: str,
    git_commits: str = "",
) -> None:
    """追加一条会话进度记录。"""
    progress_path = Path(project_dir) / "claude-progress.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    entry = f"""
=== 会话 {session_num} ({timestamp}) ===
功能：{feature_description}
状态：{status}
变更：{summary}
Git 提交：{git_commits}
"""
    with open(progress_path, "a", encoding="utf-8") as f:
        f.write(entry)


def init_progress_file(project_dir: str | Path, project_name: str) -> None:
    """初始化进度文件（首次创建）。"""
    progress_path = Path(project_dir) / "claude-progress.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    content = f"""# {project_name} - Agent 进度跟踪
# 此文件由编排系统自动管理，记录每个 Agent 会话的工作内容。
# 每个新会话开始时应先阅读此文件以了解之前的工作进展。

=== 会话 1 ({timestamp}) ===
功能：项目初始化
状态：已完成
变更：创建项目脚手架、features.json、init.sh、此进度文件
Git 提交：初始提交
"""
    progress_path.write_text(content, encoding="utf-8")


def get_last_session_num(project_dir: str | Path) -> int:
    """获取最后一个会话的编号。"""
    content = read_progress(project_dir)
    if not content:
        return 0

    last_num = 0
    for line in content.splitlines():
        if line.startswith("=== 会话 "):
            try:
                num_str = line.split("会话 ")[1].split(" ")[0]
                num = int(num_str)
                if num > last_num:
                    last_num = num
            except (IndexError, ValueError):
                continue
    return last_num
