"""项目规格和运行时配置的数据模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProjectSpec(BaseModel):
    """用户提供的项目规格说明。"""

    name: str = Field(description="项目名称，如 'my-chat-app'")
    description: str = Field(description="项目高层描述")
    tech_stack: list[str] = Field(description="技术栈列表，如 ['react', 'typescript']")
    requirements: list[str] = Field(description="功能需求列表（自然语言）")
    dev_server_command: str = Field(
        default="npm run dev", description="开发服务器启动命令"
    )
    test_command: str | None = Field(default=None, description="测试命令")
    port: int = Field(default=3000, description="开发服务器端口")
    extra_instructions: str = Field(
        default="", description="给 Agent 的额外指令"
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> ProjectSpec:
        """从 YAML 文件加载项目规格。"""
        with open(path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return cls(**data)


class RuntimeConfig(BaseModel):
    """编排器运行时配置。"""

    project_dir: str = Field(description="项目创建/工作目录")
    spec_path: str = Field(description="项目规格 YAML 文件路径")
    model: str = Field(
        default="claude-sonnet-4-6", description="Claude 模型 ID"
    )
    max_sessions: int = Field(default=50, description="最大会话数（安全上限）")
    max_turns_per_session: int = Field(
        default=30, description="每个会话的最大轮次"
    )
    max_budget_usd: float | None = Field(
        default=None, description="总预算上限（美元）"
    )
    use_playwright: bool = Field(
        default=False, description="是否启用 Playwright 浏览器测试"
    )
    permission_mode: str = Field(
        default="bypassPermissions",
        description="权限模式：bypassPermissions / acceptEdits / default",
    )
    dry_run: bool = Field(
        default=False, description="试运行模式，不实际调用 Claude"
    )
    verbose: bool = Field(default=False, description="详细输出")

    @property
    def project_path(self) -> Path:
        return Path(self.project_dir)

    @property
    def features_path(self) -> Path:
        return self.project_path / "features.json"

    @property
    def progress_path(self) -> Path:
        return self.project_path / "claude-progress.txt"

    @property
    def init_script_path(self) -> Path:
        return self.project_path / "init.sh"


class Feature(BaseModel):
    """单个功能项。"""

    id: int
    category: str = Field(description="功能类别：setup / core / ui / polish")
    priority: int = Field(description="优先级，数字越小优先级越高")
    description: str = Field(description="功能描述")
    steps: list[str] = Field(description="验证步骤")
    passes: bool = Field(default=False, description="是否已通过验证")
    skipped: bool = Field(default=False, description="是否已跳过（停滞时标记）")


class SessionResult(BaseModel):
    """单次 Agent 会话的结果。"""

    session_id: str | None = None
    success: bool = False
    feature_id: int | None = None
    feature_passed: bool = False
    summary: str = ""
    error: str | None = None
