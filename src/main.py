"""CLI 入口：解析命令行参数，启动编排器。

用法：
    python -m src.main run specs/my-project.yaml [选项]
    python -m src.main status <project-dir>
    python -m src.main resume <project-dir> --spec specs/my-project.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from .config import ProjectSpec, RuntimeConfig
from .orchestrator import run, show_status

# 修复 Windows 终端 GBK 编码问题：强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

console = Console(force_terminal=True)


def setup_logging(verbose: bool = False) -> None:
    """配置日志系统。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="长时间运行 Agent 编排器 - 基于 Anthropic 工程博客",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 从规格文件启动新项目
    python -m src.main run specs/chat-app.yaml

    # 指定项目目录和模型
    python -m src.main run specs/chat-app.yaml --project-dir ./output/chat-app --model claude-opus-4-6

    # 查看项目状态
    python -m src.main status ./output/chat-app

    # 从中断处恢复
    python -m src.main resume ./output/chat-app --spec specs/chat-app.yaml

    # 试运行（不调用 Claude）
    python -m src.main run specs/chat-app.yaml --dry-run
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ===== run 命令 =====
    run_parser = subparsers.add_parser("run", help="从规格文件启动新项目")
    run_parser.add_argument(
        "spec_path",
        help="项目规格 YAML 文件路径",
    )
    run_parser.add_argument(
        "--project-dir",
        help="项目输出目录（默认: ./output/<项目名>）",
    )
    run_parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude 模型 ID（默认: claude-sonnet-4-6）",
    )
    run_parser.add_argument(
        "--max-sessions",
        type=int,
        default=50,
        help="最大会话数（默认: 50）",
    )
    run_parser.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="每个会话最大轮次（默认: 30）",
    )
    run_parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="总预算上限（美元）",
    )
    run_parser.add_argument(
        "--playwright",
        action="store_true",
        help="启用 Playwright 浏览器测试",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行模式，不实际调用 Claude",
    )
    run_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出",
    )

    # ===== status 命令 =====
    status_parser = subparsers.add_parser("status", help="查看项目状态")
    status_parser.add_argument(
        "project_dir",
        help="项目目录路径",
    )

    # ===== resume 命令 =====
    resume_parser = subparsers.add_parser("resume", help="从中断处恢复")
    resume_parser.add_argument(
        "project_dir",
        help="项目目录路径",
    )
    resume_parser.add_argument(
        "--spec",
        required=True,
        help="项目规格 YAML 文件路径",
    )
    resume_parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude 模型 ID",
    )
    resume_parser.add_argument(
        "--max-sessions",
        type=int,
        default=50,
        help="最大会话数",
    )
    resume_parser.add_argument(
        "--max-turns",
        type=int,
        default=30,
        help="每个会话最大轮次",
    )
    resume_parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="总预算上限（美元）",
    )
    resume_parser.add_argument(
        "--playwright",
        action="store_true",
        help="启用 Playwright 浏览器测试",
    )
    resume_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细输出",
    )

    return parser.parse_args()


async def async_main() -> None:
    """异步主函数。"""
    args = parse_args()

    if not args.command:
        console.print("[red]请指定命令。使用 --help 查看帮助。[/red]")
        sys.exit(1)

    if args.command == "status":
        config = RuntimeConfig(
            project_dir=str(Path(args.project_dir).resolve()),
            spec_path="",
        )
        await show_status(config)
        return

    # run 或 resume 命令
    spec_path = args.spec_path if args.command == "run" else args.spec
    verbose = getattr(args, "verbose", False)

    setup_logging(verbose)

    # 加载项目规格
    try:
        spec = ProjectSpec.from_yaml(spec_path)
    except FileNotFoundError:
        console.print(f"[red]找不到规格文件: {spec_path}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]解析规格文件失败: {e}[/red]")
        sys.exit(1)

    # 确定项目目录
    if args.command == "run":
        project_dir = (
            args.project_dir
            if args.project_dir
            else str(Path("./output") / spec.name)
        )
    else:
        project_dir = args.project_dir

    project_dir = str(Path(project_dir).resolve())

    # 构建运行时配置
    config = RuntimeConfig(
        project_dir=project_dir,
        spec_path=str(Path(spec_path).resolve()),
        model=args.model,
        max_sessions=args.max_sessions,
        max_turns_per_session=args.max_turns,
        max_budget_usd=args.budget,
        use_playwright=args.playwright,
        dry_run=getattr(args, "dry_run", False),
        verbose=verbose,
    )

    console.print(f"[dim]项目目录: {project_dir}[/dim]")
    console.print(f"[dim]规格文件: {spec_path}[/dim]")

    # 运行编排器
    await run(spec, config)


def cli_main() -> None:
    """CLI 入口点（同步包装）。"""
    asyncio.run(async_main())


if __name__ == "__main__":
    cli_main()
