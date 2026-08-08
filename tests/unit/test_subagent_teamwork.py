from __future__ import annotations

from windcode.domain.subagents import CollaborationMode
from windcode.runtime.subagents.teamwork import infer_collaboration_mode


def test_natural_language_mode_inference() -> None:
    assert infer_collaboration_mode("让多个专家辩论并质疑证据") is CollaborationMode.NEGOTIATION
    assert infer_collaboration_mode("并行分工实现三个模块然后合并") is CollaborationMode.DIVISION
    assert (
        infer_collaboration_mode("分工调研不同方案,再互相评审并达成共识")
        is CollaborationMode.HYBRID
    )
    assert infer_collaboration_mode("共同处理复杂任务") is CollaborationMode.HYBRID
