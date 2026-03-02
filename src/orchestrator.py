"""主编排器：管理多会话 Agent 生命周期。

核心循环：
1. 检查项目是否已初始化，否则运行初始化 Agent
2. 循环：加载功能列表 → 选择下一个功能 → 运行编码 Agent → 检查进度
3. 停滞检测：同一功能连续失败 N 次则跳过
4. 可选：运行验证 Agent 做最终端到端测试
5. 输出最终报告
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .agents.coder import run_coder
from .agents.initializer import is_initialized, run_initializer
from .agents.verifier import run_verifier
from .config import ProjectSpec, RuntimeConfig, SessionResult
from .features import (
    get_progress_summary,
    load_features,
    mark_skipped,
    pick_next,
)
from .progress import append_progress, get_last_session_num

logger = logging.getLogger("orchestrator")
console = Console()

# 停滞检测阈值：同一功能连续失败多少次后跳过
STALL_THRESHOLD = 3


async def run(spec: ProjectSpec, config: RuntimeConfig) -> None:
    """运行完整的编排流程。"""
    # 清除 CLAUDECODE 环境变量，允许 SDK 启动子 Agent 会话
    # （从 Claude Code 内部运行时需要此步骤，否则会报嵌套会话错误）
    os.environ.pop("CLAUDECODE", None)

    console.print(
        Panel(
            f"[bold blue]长时间运行 Agent 编排器[/bold blue]\n"
            f"项目: {spec.name}\n"
            f"模型: {config.model}\n"
            f"最大会话数: {config.max_sessions}",
            title="启动",
        )
    )

    start_time = time.time()
    session_results: list[SessionResult] = []

    # ===== 阶段 1：初始化 =====
    if not is_initialized(config.project_dir):
        console.print("\n[bold yellow]阶段 1：项目初始化[/bold yellow]")

        if config.dry_run:
            console.print("[dim]（试运行模式）将运行初始化 Agent[/dim]")
        else:
            result = await run_initializer(spec, config)
            session_results.append(result)

            if not result.success:
                console.print(
                    f"[bold red]初始化失败: {result.summary}[/bold red]"
                )
                if result.error:
                    console.print(f"[red]错误: {result.error}[/red]")
                return

            console.print(f"[green]初始化成功: {result.summary}[/green]")
    else:
        console.print("[dim]项目已初始化，跳过阶段 1[/dim]")

    # ===== 阶段 2：功能实现循环 =====
    console.print("\n[bold yellow]阶段 2：功能实现[/bold yellow]")

    stall_count = 0
    last_feature_id: int | None = None
    session_num = get_last_session_num(config.project_dir) + 1

    for loop_idx in range(config.max_sessions):
        # 加载最新功能列表
        features = load_features(config.project_dir)
        next_feature = pick_next(features)

        if next_feature is None:
            console.print(
                "\n[bold green]所有功能已完成！[/bold green]"
            )
            break

        # 进度概览
        summary = get_progress_summary(features)
        console.print(f"\n[cyan]进度: {summary}[/cyan]")
        console.print(
            f"[bold]会话 {session_num}: "
            f"功能 #{next_feature.id} - {next_feature.description}[/bold]"
        )

        # 停滞检测
        if last_feature_id == next_feature.id:
            stall_count += 1
            if stall_count >= STALL_THRESHOLD:
                console.print(
                    f"[yellow]功能 #{next_feature.id} 连续失败 "
                    f"{STALL_THRESHOLD} 次，跳过此功能[/yellow]"
                )
                mark_skipped(config.project_dir, next_feature.id)
                append_progress(
                    config.project_dir,
                    session_num,
                    next_feature.description,
                    "已跳过",
                    f"连续 {STALL_THRESHOLD} 次未能完成，自动跳过",
                )
                stall_count = 0
                last_feature_id = None
                session_num += 1
                continue
        else:
            stall_count = 0
            last_feature_id = next_feature.id

        if config.dry_run:
            console.print(
                f"[dim]（试运行）将实现功能 #{next_feature.id}[/dim]"
            )
            session_num += 1
            continue

        # 运行编码 Agent
        result = await run_coder(
            feature=next_feature,
            spec=spec,
            config=config,
            session_num=session_num,
        )
        session_results.append(result)

        # 记录结果
        status = "已完成" if result.feature_passed else "未完成"
        if result.feature_passed:
            console.print(f"  [green]功能 #{next_feature.id} 已通过[/green]")
        else:
            console.print(
                f"  [red]功能 #{next_feature.id} 未通过[/red]"
            )

        # 如果 Agent 没有自动写入进度，由编排器补写
        append_progress(
            config.project_dir,
            session_num,
            next_feature.description,
            status,
            result.summary,
        )

        session_num += 1

    else:
        console.print(
            f"\n[yellow]已达到最大会话数 ({config.max_sessions})，停止循环[/yellow]"
        )

    # ===== 阶段 3：最终验证（可选）=====
    if config.use_playwright and not config.dry_run:
        console.print("\n[bold yellow]阶段 3：端到端验证[/bold yellow]")
        verify_result = await run_verifier(spec, config)
        session_results.append(verify_result)
        console.print(f"[cyan]{verify_result.summary}[/cyan]")

    # ===== 最终报告 =====
    elapsed = time.time() - start_time
    _print_final_report(config, session_results, elapsed)


def _print_final_report(
    config: RuntimeConfig,
    results: list[SessionResult],
    elapsed: float,
) -> None:
    """输出最终报告。"""
    features = load_features(config.project_dir)
    summary = get_progress_summary(features)

    # 统计
    total_sessions = len(results)
    successful = sum(1 for r in results if r.success)
    features_passed = sum(1 for r in results if r.feature_passed)

    # 构建表格
    table = Table(title="编排报告")
    table.add_column("指标", style="cyan")
    table.add_column("值", style="green")

    table.add_row("总会话数", str(total_sessions))
    table.add_row("成功会话", str(successful))
    table.add_row("通过功能数", str(features_passed))
    table.add_row("功能进度", summary)
    table.add_row("总耗时", f"{elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")

    console.print("\n")
    console.print(Panel(table, title="[bold]最终报告[/bold]"))

    # 列出未完成的功能
    remaining = [f for f in features if not f.passes and not f.skipped]
    if remaining:
        console.print(f"\n[yellow]未完成功能 ({len(remaining)}):[/yellow]")
        for f in remaining:
            console.print(f"  #{f.id} [{f.category}] {f.description}")

    skipped = [f for f in features if f.skipped]
    if skipped:
        console.print(f"\n[red]已跳过功能 ({len(skipped)}):[/red]")
        for f in skipped:
            console.print(f"  #{f.id} [{f.category}] {f.description}")


async def show_status(config: RuntimeConfig) -> None:
    """显示项目当前状态。"""
    if not is_initialized(config.project_dir):
        console.print("[yellow]项目尚未初始化。[/yellow]")
        return

    features = load_features(config.project_dir)
    summary = get_progress_summary(features)

    console.print(Panel(f"[bold]{summary}[/bold]", title="项目状态"))

    table = Table()
    table.add_column("ID", style="dim")
    table.add_column("类别")
    table.add_column("描述")
    table.add_column("状态")

    for f in features:
        if f.passes:
            status = "[green]已通过[/green]"
        elif f.skipped:
            status = "[red]已跳过[/red]"
        else:
            status = "[yellow]待完成[/yellow]"
        table.add_row(str(f.id), f.category, f.description, status)

    console.print(table)

    last_session = get_last_session_num(config.project_dir)
    console.print(f"\n最后会话: #{last_session}")
