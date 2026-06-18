"""View 层支持(M3)。harness 侧的 View 参考实现:动画投影(ADR-020)。

View 本体是独立前端(Tauri+Phaser+React,ADR-001),自行从 Session 派生动画状态。
本包提供权威的 Python 参考投影,供 TS 端镜像、供后端 catch-up 做快照,且可离线测试。
"""
from harness.view.projection import project

__all__ = ["project"]
