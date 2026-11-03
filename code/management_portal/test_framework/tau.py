from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.dev_target import VerifiedDevTarget, resolve_unique_dev


from management_common.site_config import read_site_config
from management_portal.test_framework.paths import state_root
from management_portal.test_framework.compatibility import test_actor

DEFAULT_STORAGE_STATE = Path(str(state_root() / "test-actor-storage-state.json")).expanduser()
RESULT_MARKER = "PORTAL_TAU_RESULT:"
TAU_PLAYWRIGHT = ROOT / "management_portal" / "test_framework" / "tau_runtime" / "node_modules" / "playwright"


def executor_runtime_issue() -> str:
    """Check the platform's launcher; actual ownership is enforced at spawn."""
    if platform.system() == "Windows":
        try:
            import psutil  # noqa: F401 -- native durable process identities
            from management_common import process_runtime  # noqa: F401
        except ImportError:
            return "TAU 缺少 Windows 进程管理依赖；请运行 bootstrap_portal install tau。"
        return ""
    if platform.system() != "Linux":
        return "TAU 在此平台尚未接入原生执行器；当前支持 Windows Job 或 Linux systemd-run。"
    if not shutil.which("systemd-run") or not Path("/proc").is_dir():
        return "TAU 执行器需要 systemd-run 和 /proc。"
    return ""


def browser_runtime_config() -> dict[str, str]:
    """Carry the executor's browser location across Resident environments."""
    browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers:
        if sys.platform == "win32":
            cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        elif sys.platform == "darwin":
            cache = Path.home() / "Library/Caches"
        else:
            cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        browsers = str(cache / "ms-playwright")
    if browsers != "0" and not Path(browsers).is_absolute():
        browsers = str((Path(os.environ.get("INIT_CWD") or os.getcwd()) / browsers).absolute())
    return {"python_executable": sys.executable, "browsers_path": browsers}


# Chromium's path per (node, Playwright install): asking node for it takes about 0.2 s.
_BROWSER_PATHS: dict[tuple[str, int], str] = {}


def browser_runtime_issue() -> str:
    """Check the optional TAU browser, independently of the removed UI test suite.

    Node reports Chromium's path once per Playwright install (the package
    directory's modification time); a failed lookup is asked again next time,
    and the browser file itself is checked on every call.
    """
    node = shutil.which("node")
    if not node:
        return "TAU 需要 Node.js；请安装 TAU 依赖档。"
    if not TAU_PLAYWRIGHT.is_dir():
        return "TAU 缺少 Playwright；请运行 bootstrap_portal install tau。"
    install = (node, TAU_PLAYWRIGHT.stat().st_mtime_ns)
    browser = _BROWSER_PATHS.get(install)
    if browser is None:
        try:
            browser = subprocess.check_output(
                [node, "-e", "process.stdout.write(require(process.argv[1]).chromium.executablePath())", str(TAU_PLAYWRIGHT)],
                text=True, stderr=subprocess.PIPE, timeout=10,
            ).strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return "TAU 的 Playwright 运行时无法加载；请重新运行 bootstrap_portal install tau。"
        _BROWSER_PATHS.clear()
        _BROWSER_PATHS[install] = browser
    if not Path(browser).is_file():
        return "TAU 缺少 Chromium；请运行 bootstrap_portal install tau。"
    return ""


class CaseNotSent(InterruptedError):
    """A precondition of a TAU case failed before the case reached a Resident.

    The case did not run, so its result is ``unsupported``: a missing
    precondition, never a verdict about Portal.
    """


@dataclass(frozen=True)
class TauConfig:
    storage_state: Path
    provider: str = "codex"
    model: str = ""
    enabled: bool = True
    deployment_registry: Path | None = None

    @classmethod
    def from_environment(cls) -> "TauConfig":
        enabled_value = os.getenv("PORTAL_TEST_TAU_ENABLED", "")
        disabled = enabled_value.strip().lower() in {
            "0", "false", "no", "off"
        }
        return cls(
            storage_state=Path(os.getenv("PORTAL_TEST_STORAGE_STATE", str(DEFAULT_STORAGE_STATE))).expanduser(),
            provider=os.getenv("PORTAL_TEST_TAU_PROVIDER", "codex").strip() or "codex",
            model=os.getenv("PORTAL_TEST_TAU_MODEL", "").strip(),
            enabled=not disabled,
        )

    def unavailable_reason(self) -> str:
        if not self.enabled:
            return "已通过 PORTAL_TEST_TAU_ENABLED 禁用。"
        issue = executor_runtime_issue()
        if issue:
            return issue
        try:
            mode = self.storage_state.stat().st_mode
        except OSError:
            return "尚未创建 test-actor 浏览器存储状态。"
        if not stat.S_ISREG(mode):
            return "test-actor 浏览器存储状态不是普通文件。"
        if os.name != "nt" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            return "test-actor 浏览器存储状态的权限必须为 0600。"
        issue = browser_runtime_issue()
        if issue:
            return issue
        try:
            resolve_unique_dev(self.deployment_registry)
        except Exception as exc:
            return str(exc)
        return ""

    @property
    def available(self) -> bool:
        return not self.unavailable_reason()


RUNTIME_LABELS = {"model": "模型", "reasoning_effort": "推理强度", "permission_preset": "权限预设"}


def build_prompt(
    planned: dict[str, Any],
    config: TauConfig,
    run_dir: Path,
    target: VerifiedDevTarget,
    run_id: str,
    *,
    assignment: dict[str, Any] | None = None,
) -> str:
    runner_prompt = (ROOT / "management_portal/test_framework/config/tau_runner_prompt.md").read_text(encoding="utf-8").strip().replace("<test-actor>", test_actor())
    runner_prompt = (runner_prompt
        .replace("<rtt-python-json>", json.dumps(sys.executable))
        .replace("<rtt-script-json>", json.dumps(str(ROOT / 'management_portal/test_framework/tools/record_tool_rtt.py')))
        .replace("<rtt-evidence-json>", json.dumps(str(run_dir))))
    from management_portal.test_framework.tau_fixture_paths import fixture_root
    task_prompt = (str(planned["implementation"].get("task_prompt") or "").strip()
        .replace("<test-actor>", test_actor())
        .replace("<tau-native-forms-fixture>", str(ROOT / "tests/cases/fixtures/tau_mcp_forms.py")))
    from management_portal.test_framework.tau_cleanup import team_marker

    marker = team_marker(run_id, str(planned["module"]))
    fixture_reason = planned["implementation"].get("lineage_fixture", "")
    if not isinstance(fixture_reason, str):
        raise ValueError("Invalid TAU lineage_fixture")
    fixture_instruction = (
        f"本用例允许独立临时 Lineage，必要性：{fixture_reason}。"
        "仅为上述测试对象建立最小独立图，业务功能执行入口继续使用产品真实固定绑定。"
        "团队标题从创建、Fork 为新团队到重命名均须保留下述完整标记。"
        if fixture_reason else
        "本用例没有独立团队声明：复用固定 Lineage，禁止因新 Run 或准备测试样本而新建团队。"
        "旧 Task Prompt 中笼统要求临时团队的语句不构成例外；无法在此范围内验证时报告缺口。"
    )
    if planned["implementation"].get("standalone_git_fixture"):
        fixture_instruction += (
            " 本用例声明需在 Git checkout 外准备未初始化目录。"
            "框架已在 run-context.json 的 standalone_fixture_dir 提供本用例专属位置；"
            "仅在其下创建业务夹具，证据和 helper 仍写证据目录。"
            "首次登记前确认候选目录不在任何既有 Git worktree 内。"
            "结束通过正式入口注销本轮资源，再由框架回收该目录；不删除或移动其他目录。"
        )
    account_instruction = ""
    case_instructions = str(planned.get("case_instructions") or "").strip()
    if assignment and assignment.get("backend_route_id"):
        account_instruction = (
            f"\n本模块使用 Codex 账号 {assignment['backend_route_id']}，"
            f"service tier 为 {(assignment.get('runtime_options') or {}).get('service_tier') or 'standard'}。"
            "上述账号与速度设置仅用于执行测试的 TAU Resident，不约束被测业务或临时 Resident。"
            "被测业务使用页面当前支持的默认配置；仅当用例明确指定账号或速度时，"
            "才通过正式页面或用例允许的控制接口选择并验证该设置。"
            "不要因业务页面没有账号或速度选择器而判定前置条件失败。"
            "真实的权限、额度或模型错误仍须记录，不要修改全局账号默认值或其他用户的 Resident。\n"
        )
    resident_account = planned.get("resident_account_id")
    resident_runtime = planned.get("resident_runtime") or {}
    if resident_account:
        account_instruction = (
            ("\n除本轮补充要求明确限定的例外步骤外，" if case_instructions else "\n")
            + f"本次 Campaign 可配置测试对象范围内的全部 Codex Resident 必须使用账号 {resident_account}，"
            "范围包括本次可控制的 TAU 执行器，以及本用例新建或 Fork 的被测业务成员、讨论成员和临时成员。"
            "独立 API 模型保持原 Provider 与模型，不转换为 Codex。"
            "这是本次运行发起者明确授权的账号配置要求，优先于用例中的历史账号选择。"
            "每次发送消息或触发 AI 工作前，通过正式页面或 HATG Instruction 核对实际 Session 账号；"
            "需要切换时使用普通账号切换/Session 迁移入口，保留 Resident 身份，等待配置就绪后再发送。"
            "新建与 Fork 时，页面提供账号选择的就显式选择该账号；Fork 不提供选择而沿用来源成员设置，"
            "发送前照样核对，不一致再用上述入口调整，不因页面没有选择器判定失败。"
            + ("禁止使用未在本轮补充要求中明确授权的账号作为备用路线。" if case_instructions else
               "禁止使用其他账号作为失败后的备用路线。")
            + (
                "同一范围内的 Codex Resident 还必须使用"
                + "、".join(f"{RUNTIME_LABELS[key]} {value}" for key, value in resident_runtime.items())
                + "：新建时页面提供的设置显式选择，页面未提供或 Fork 沿用来源设置时，"
                "发送前用 GetResidentSettings 核对，不一致通过 UpdateResidentSettings 调整；已有成员同样处理。"
                "只调整本次测试会调用的成员，不修改全局账号默认值或无关 Resident。"
                if resident_runtime else
                "只调整本次测试会调用的成员，不修改全局账号默认值或无关 Resident，保留原模型与速度设置。"
            ) +
            "页面没有提供的账号或运行设置选择（例如 Fork 与新建表单中没有的项）不是用例步骤，"
            "不要记为覆盖缺口或失败，只需在发送前核对实际设置。"
            "产品在被测流程中自行创建并立即启动的成员（例如 WALL 分配的工作成员），"
            "以及本用例复用的产品固定绑定共享业务成员（例如开发投影绑定的 Family、论文绑定的 Research Chat Resident），"
            "记录其实际账号与设置后照常使用，不以上述约束判定失败，也不为迎合约束修改它们或产品默认值。"
            "即使当前权限允许修改，也不能为本轮账号要求迁移或改写这些共享成员；实际额度不足时记录执行前提缺口。"
            "把所调用成员的 Resident ID、Lineage ID 与实际账号写入本用例证据。"
            "无权配置或账号不可用时如实报告阻塞，不在错误账号上执行；涉及跨账号行为的用例"
            "若与本次约束冲突，记录不能完成的验收条件，不伪报通过。\n"
        )
    supplemental_instruction = (
        "本次 Run 发起者对本用例的补充要求（仅作用于当前用例，不修改用例正文、站点默认或账号登记）：\n"
        + case_instructions
        + "\n仅明确列出的步骤使用例外授权，其余仍遵守本轮默认约束；完成后核验要求的恢复状态并留证。"
        if case_instructions else ""
    )
    return f"""{runner_prompt}

运行环境：
- 本次运行：{run_id}；测试用例：{planned['case_title']}
- 部署：{target.deployment_id}；入口：{target.origin}；身份：{target.actor}
- 初始 build：由 TAU preflight 核对，已写入证据目录的 run-context.json
- 浏览器登录状态：{config.storage_state}
- 浏览器助手：{ROOT / 'management_portal/test_framework/tau_browser.js'}
- 证据目录：{run_dir}；辅助脚本目录：{run_dir / 'helpers'}
- 测试工程、条目来源与样例文件目录：{fixture_root(run_dir)}
- 仓库根目录：{ROOT}；Python：{sys.executable}
- 本模块临时资源标记：{marker}

{account_instruction}
{supplemental_instruction}
{fixture_instruction}

像真实用户一样完成目标，记录过程、遇到的问题和性能体验。
缺少截图、计时或因果记录时写“未记录”，可以补充观察并说明观察时间，不据此判定产品失败。
用例与当前设计不符时修正用例，归为用例问题；操作偏差归为执行偏差，助手错误归为工具问题。
发布、重启、短暂网络中断后等待或刷新并继续。只有产品恢复不了、操作卡死、
数据丢失或需要人工处理时才记录产品问题。不要为补证据重复已受理的业务操作。

从页面现有控件发现可用操作，以用户可见的完成状态判断流程进展，不猜 role 或内部字段。
可用 portal.action 记录关键操作的耗时；截图、原始响应、日志都是报告材料，
公共助手自动保存的观察也可引用。无法记录的项明确注明未记录，不强制完整因果链。
run-context.json 保存本轮上下文；身份或目标异常时停止业务操作并报告，不能冒用其他账号。
恢复后可用 portal.verifyIdentity() 核对身份。不得输出登录凭据或私人内容。

Paper、Repo、WALL 等使用产品绑定的固定 Lineage/Resident。
临时 Resident、Fork 子成员和获准创建的 Lineage 保留上述完整标记。
仅通过正式页面或 Instruction 清理本轮自建对象，固定团队与执行器保留。
创建后记录正式 ID，重试复用 ID；有工作未结束或归属不明时保留并说明。
证据目录不能登记或绑定为 Workspace。普通工程和样例放 fixture_dir；
产品目录契约优先，Article 使用有权访问的已登记 Project 内本轮独占新子目录，
写入前核对路径与权限并记录清理边界，不清理既有文稿或 Project 根目录。
测试结束框架清理 fixtures、helpers 和进程，观察材料保留供报告查阅。
使用共享浏览器助手携带 run-context.json 的资源生命周期标识，新建资源自动登记由框架回收。
已有工程内新建的 WALL 条目写入 test-fixtures.json 的 wall_items（project、block）；
新建 CreateUpload 句柄写入 uploads 数组。不要把已有资源、输入资源、固定团队或内部 block ID 登记为待注销资源。
消息、执行和通知历史按产品保留，不清空共享收件箱。

自然语言描述：{planned.get('description', '')}
用户任务：
{task_prompt}

完成后先给出自然语言使用报告，然后单独一行输出 JSON（不使用代码围栏）：
{RESULT_MARKER} {{"status":"reported","summary":"使用总结","process":{{"status":"completed 或 blocked 或 not_started","steps":[],"stopped_at":"","reason":""}},"issues":[{{"category":"product 或 case 或 execution 或 tool","severity":"blocking 或 impact 或 minor","step":"发生步骤","symptom":"现象","evidence":[]}}],"performance":[{{"step":"关键步骤","duration_ms":null,"observation":"未记录"}}],"evidence":[],"evidence_gaps":[]}}
问题分别归为产品问题、用例问题、执行偏差、工具问题，严重程度为阻断、影响使用、小问题。
没有问题时 issues 为空。流程未走完时说明卡在哪一步及原因，不输出 PASS/FAIL。
"""


def parse_result(message: str) -> dict[str, Any]:
    payload_text = ""
    for line in reversed(str(message or "").splitlines()):
        stripped = line.strip()
        if stripped.startswith(RESULT_MARKER):
            payload_text = stripped[len(RESULT_MARKER):].strip()
            break
    if not payload_text:
        raise ValueError("TAU Resident 未返回 PORTAL_TAU_RESULT")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("TAU Resident 返回了无效的结果 JSON") from exc
    from management_portal.test_framework.tau_usage_report import usage_report

    return usage_report(payload)


ResidentInvoker = Callable[..., dict[str, Any]]
