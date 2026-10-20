from __future__ import annotations

BUILTIN = {"name": "wall", "title": "WALL", "path": "/development?view=wall", "description": "设计驱动的软件开发工作台"}


def work_flow() -> dict:
    return {
        "name": "wall:work",
        "title": "WALL · 任务",
        "prompt": "你在执行 WALL 工程流程。以本次输入指定的工程、设计点、操作及权限范围为准。保留真实验证证据，按 WALL_RESULT 协议报告结果。纯讨论不能声称代码已实现；没有验证不得声称成功。业务产物通过相应资源入口登记。",
    }


def definitions(scope: str) -> list[dict]:
    if not scope.startswith(("wall:", "wall-work:")):
        return []
    return [work_flow()]
