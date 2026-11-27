"""Initialize the contents of a newly staged Workspace."""
from pathlib import Path


def register_owned_workspace(owner_id, path, creation, connection):
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service.identity_contract import creation_candidate_id
    get_workspace_store().ensure(
        creation_candidate_id("workspace"), str(path), owner_id=owner_id,
        operation_id=creation.key, child_of_operation=True, connection=connection,
    )


WORKSPACE_QUOTAS = {"Resident": (8, 32, 72), "Lineage": (32, 128, 168),
                    "Project": (64, 256, 168), "Independent": (8, 32, 72)}


def create_workspace_folder(path: Path, role: str, *, description="", language="zh-CN") -> None:
    normal, peak, hours = WORKSPACE_QUOTAS[role]
    path.mkdir(parents=True, exist_ok=True)
    (path / "HDD").mkdir()
    policy = (
        f"SSD 常态软配额 {normal} GiB，加速期总额度 {peak} GiB，期限 {hours} 小时。"
        "高频读写可以使用 SSD；使用加速额度时记录用途、目录、预计峰值、维护人和到期时间。"
        "任务结束后 48 小时内核对归档和清理。整盘用量达到 85% 时复核，达到 90% 时协调新增或续期。\n\n"
        "大型生成数据和权重按需写入 HDD。所有 HDD 软链放在 HDD/，其中 generated_data 和 "
        "generated_weight 各最多一个，共享资源链接按需创建。首次建链时建立 HDD/INDEX.md，"
        "登记链接名、类别、真实路径、用途或版本和维护人。\n"
    )
    if language == "en":
        policy = (
            f"SSD allowance {normal} GiB, up to {peak} GiB for {hours} hours for active work. "
            "Record the purpose, directory, expected peak, maintainer and expiration. "
            "Review within 48 hours of task completion. Review disk usage at 85%, coordinate extensions at 90%.\n\n"
            "Store large generated data and weights on HDD as needed. Place HDD links in HDD/. "
            "Use at most one generated_data and one generated_weight link. Shared links are unlimited. "
            "At first use record link name, category, target, purpose or version and maintainer in HDD/INDEX.md.\n"
        )
    (path / "README.md").write_text(
        f"# {role} Workspace\n\n" + (description + "\n\n" if description else "") + policy, encoding="utf-8"
    )
