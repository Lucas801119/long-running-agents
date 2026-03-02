"""功能列表管理：加载、保存、选择下一个功能、安全更新。

文章核心洞察：使用 JSON 格式存储功能列表（模型不容易破坏结构化数据），
Agent 只允许修改 passes 字段，防止意外删除或修改功能描述。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Feature


def load_features(project_dir: str | Path) -> list[Feature]:
    """从 features.json 加载功能列表。"""
    features_path = Path(project_dir) / "features.json"
    if not features_path.exists():
        return []
    with open(features_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Feature(**item) for item in data]


def save_features(project_dir: str | Path, features: list[Feature]) -> None:
    """保存功能列表到 features.json。"""
    features_path = Path(project_dir) / "features.json"
    data = [feat.model_dump() for feat in features]
    with open(features_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def pick_next(features: list[Feature]) -> Feature | None:
    """返回优先级最高的未完成且未跳过的功能。

    按 priority 升序排列，返回第一个 passes=False 且 skipped=False 的功能。
    """
    pending = [f for f in features if not f.passes and not f.skipped]
    if not pending:
        return None
    return min(pending, key=lambda f: f.priority)


def safe_update_passes(
    project_dir: str | Path, feature_id: int, passes: bool
) -> bool:
    """安全更新单个功能的 passes 状态。

    仅修改指定 feature_id 的 passes 字段，不触碰其他任何内容。
    返回是否成功更新。
    """
    features = load_features(project_dir)
    for feat in features:
        if feat.id == feature_id:
            feat.passes = passes
            save_features(project_dir, features)
            return True
    return False


def mark_skipped(project_dir: str | Path, feature_id: int) -> bool:
    """将指定功能标记为已跳过（停滞时使用）。"""
    features = load_features(project_dir)
    for feat in features:
        if feat.id == feature_id:
            feat.skipped = True
            save_features(project_dir, features)
            return True
    return False


def safe_update_from_agent(
    project_dir: str | Path, new_features_data: list[dict]
) -> tuple[bool, str]:
    """安全地从 Agent 输出更新功能列表。

    只允许修改 passes 字段。如果 Agent 试图修改结构（添加/删除/修改描述），
    则拒绝更新并返回错误信息。
    """
    current = load_features(project_dir)
    current_map = {f.id: f for f in current}

    # 检查结构完整性
    if len(new_features_data) != len(current):
        return False, (
            f"功能数量不匹配：当前 {len(current)} 个，"
            f"Agent 提供 {len(new_features_data)} 个。拒绝更新。"
        )

    changes: list[str] = []
    for new_item in new_features_data:
        fid = new_item.get("id")
        if fid not in current_map:
            return False, f"发现未知功能 ID: {fid}。拒绝更新。"

        old = current_map[fid]

        # 检查是否只有 passes 字段被修改
        if new_item.get("category") != old.category:
            return False, f"功能 #{fid} 的 category 被修改。拒绝更新。"
        if new_item.get("description") != old.description:
            return False, f"功能 #{fid} 的 description 被修改。拒绝更新。"
        if new_item.get("steps") != old.steps:
            return False, f"功能 #{fid} 的 steps 被修改。拒绝更新。"
        if new_item.get("priority") != old.priority:
            return False, f"功能 #{fid} 的 priority 被修改。拒绝更新。"

        new_passes = new_item.get("passes", False)
        if new_passes != old.passes:
            changes.append(
                f"功能 #{fid}: passes {old.passes} → {new_passes}"
            )
            old.passes = new_passes

    if changes:
        save_features(project_dir, current)
        return True, "更新成功：" + "; ".join(changes)

    return True, "无变更。"


def get_progress_summary(features: list[Feature]) -> str:
    """返回人类可读的进度摘要。"""
    total = len(features)
    if total == 0:
        return "无功能列表。"

    passed = sum(1 for f in features if f.passes)
    skipped = sum(1 for f in features if f.skipped)
    remaining = total - passed - skipped

    pct = (passed / total) * 100
    summary = f"{passed}/{total} 功能已完成 ({pct:.0f}%)"
    if skipped:
        summary += f"，{skipped} 个已跳过"
    if remaining:
        summary += f"，{remaining} 个待完成"
    return summary


def get_feature_by_id(
    features: list[Feature], feature_id: int
) -> Feature | None:
    """根据 ID 获取功能。"""
    for f in features:
        if f.id == feature_id:
            return f
    return None
