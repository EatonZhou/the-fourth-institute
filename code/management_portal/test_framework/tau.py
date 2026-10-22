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

from management_portal.portal_deployment_registry import DEFAULT_DEPLOYMENT_REGISTRY_PATH
from management_portal.test_framework.catalog import ROOT
from management_portal.test_framework.dev_target import VerifiedDevTarget, resolve_unique_dev


from management_common.site_compatibility import compatibility_value
from management_portal.test_framework.paths import state_root
from management_portal.test_framework.compatibility import test_actor

DEFAULT_STORAGE_STATE = Path(compatibility_value("test_framework.storage_state", str(state_root() / "test-actor-storage-state.json"))).expanduser()
RESULT_MARKER = "PORTAL_TAU_RESULT:"
TAU_PLAYWRIGHT = ROOT / "management_portal" / "test_framework" / "tau_runtime" / "node_modules" / "playwright"


def executor_runtime_issue() -> str:
    """The campaign launcher and case-owned helper cleanup are Linux-only today."""
    if platform.system() != "Linux":
        return "TAU 执行器仍依赖 Linux systemd-run 和 /proc；Windows/macOS 的执行器与进程清理尚未接入。"
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


def browser_runtime_issue() -> str:
    """Check the optional TAU browser, independently of the removed UI test suite."""
    node = shutil.which("node")
    if not node:
        return "TAU 需要 Node.js；请安装 TAU 依赖档。"
    if not TAU_PLAYWRIGHT.is_dir():
        return "TAU 缺少 Playwright；请运行 bootstrap_portal install tau。"
    try:
        browser = subprocess.check_output(
            [node, "-e", "process.stdout.write(require(process.argv[1]).chromium.executablePath())", str(TAU_PLAYWRIGHT)],
            text=True, stderr=subprocess.PIPE, timeout=10,
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "TAU 的 Playwright 运行时无法加载；请重新运行 bootstrap_portal install tau。"
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
            f"\n本次 Campaign 的全部 Codex Resident 必须使用账号 {resident_account}，"
            "范围包括 TAU 执行器、被测业务成员、讨论成员以及本用例创建或 Fork 的临时成员。"
            "独立 API 模型保持原 Provider 与模型，不转换为 Codex。"
            "这是本次运行发起者明确授权的账号配置要求，优先于用例中的历史账号选择。"
            "每次发送消息或触发 AI 工作前，通过正式页面或 HATG Instruction 核对实际 Session 账号；"
            "需要切换时使用普通账号切换/Session 迁移入口，保留 Resident 身份，等待配置就绪后再发送。"
            "新建与 Fork 时，页面提供账号选择的就显式选择该账号；Fork 不提供选择而沿用来源成员设置，"
            "发送前照样核对，不一致再用上述入口调整，不因页面没有选择器判定失败。"
            "禁止使用其他账号作为失败后的备用路线。"
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
            "以及产品已绑定、本用例无权修改的共享业务成员（例如论文绑定的 Research Chat Resident），"
            "记录其实际账号与设置后照常使用，不以上述约束判定失败，也不为迎合约束修改它们或产品默认值。"
            "把所调用成员的 Resident ID、Lineage ID 与实际账号写入本用例证据。"
            "无权配置或账号不可用时如实报告阻塞，不在错误账号上执行；涉及跨账号行为的用例"
            "若与本次约束冲突，记录不能完成的验收条件，不伪报通过。\n"
        )
    visual_instruction = ""
    if "ui" in (planned.get("aspects") or []):
        visual_instruction = """

本用例包含 UI 测试方面。必须至少保存一张能够支持结论的 PNG/JPEG 截图到证据目录，
并真正查看截图，检查布局、遮挡、溢出、层级、可读性及 Portal 共享视觉语言的一致性。
最终 JSON 必须填写 visual_review；截图文件存在本身不代表视觉验证通过。截图缺失、
无法查看或结论没有页面事实支持时，本用例必须 failed。
"""
    return f"""{runner_prompt}

运行环境：
- 本次独立运行：{run_id}
- 本模块临时资源标记：{marker}
- Portal 实例：{target.instance_id}（由本次显式测试目标配置解析并核验）
- 当前受测实例入口：{target.origin}
- 初始 build：由 TAU preflight 核对，已写入证据目录的 run-context.json
- Test 页面能力指纹：{target.page_fingerprint}
- 已验证会话身份：{target.actor}
- {test_actor()} Playwright storage state：{config.storage_state}
- 共享浏览器启动器：{ROOT / 'management_portal/test_framework/tau_browser.js'}
- 证据目录：{run_dir}
- 测试工程、条目来源与样例文件目录：{run_dir / 'fixtures'}
- 辅助脚本目录：{run_dir / 'helpers'}
- 仓库根目录：{ROOT}
- 框架 Python 解释器：{sys.executable}（本次执行器实际运行环境；单元回归仍使用 tests/cases 的隔离数据库）
{account_instruction}

{fixture_instruction}

Paper、Repo、WALL、投影工坊等功能验证必须从真实产品入口核对并使用已绑定的固定
Lineage/Resident，不能复制一套团队冒充业务投影验收。导航、筛选和只读显示复用现有对象。
用例确需独立对话、Stop、Reset 或同团队 Fork 样本时，在所有 TAU 执行器共用的 TAU
Lineage 中创建带上述完整标记的普通临时 Resident，Fork 子成员也保留标记；不操作 Leader、执行器
自身或其他既有成员。产品正常创建的业务成员遵循产品绑定；测试结束删除其测试条目、取消测试工程登记，由产品同时清理对应 Resident。
创建后保存必要性、正式 Lineage/Resident ID 和创建回执到本次证据目录，重试复用原 ID。
用例结束通过正常页面或已有 Instruction 只清理自建临时对象；固定团队保留。
模块结束时框架通过已有 DeleteResident 回收固定 TAU 团队中的标记成员，通过
DeleteLineage 回收例外独立团队，核对归属、运行状态和删除结果，保护执行器与固定团队。
仍有工作或归属不明时保留对象及错误，清理失败不得声称完成。

框架已在上述精确证据目录写入 `run-context.json`，记录本次 run_id、case_id、证据目录的 realpath
和初始 build。开始任何页面操作前回读它并核对与本次运行一致。共享浏览器助手在身份校验后、首次
页面导航前自动把变化后的 build 与读取时间追加到 build_observations，并保存启动回执；使用这份原始
回执核对代际变化，不需要事后手工补写。不得复用此前 Run 的脚本变量、工作目录或证据路径；
文件缺失、无法回读或内容与本次运行不符时立即报告 failed，不继续测试。最终报告前，
逐项用文件系统验证 evidence 中每个相对路径都真实存在于本次证据目录内，且至少一张本轮
截图可读取；此前 Run 的文件不能作为本轮证据。
共享浏览器助手在首次 goto 前安装被动取证，把实际请求的独立 ID、frame/文档实例、
开始/响应/终态与生命周期事实，以及标准浏览器资源时序追加到 `tau-browser-observations.jsonl`。
同 URL 请求不能混淆；浏览器未暴露的字段不可用，采集结束时 pending 只表示尚无终态证据，
不证明请求已到服务器，不伪造成功、取消或后端超时。日志不保存 headers、Cookie、token、
完整 URL query、请求或聊天正文；选中指令名与显式请求人数只在内存提取。
框架自动引用并保留本用例的标准日志。ResourceTiming 和 PortalEvents 关闭回执没有唯一传输
请求 ID 时明确记为无法关联，不靠 URL 或时间接近豁免取消；没有因果证据的原因仍为 unknown。
公共观测不替代每次原 Harness 工具调用 RTT、实际 UI 点击到 ready 的计时、截图或原 PASS 条件。
共享助手自动在 `tau-helper-calls.jsonl` 记录每次 `portal.instruction` 的原调用起止、成功/失败及耗时，
包括 `inputState` 与 `waitForInput` 每次独立的 GetInputState。并行调用分别计时，框架保留该文件；
这些调用无需人工重复包装。耗时包括同一幂等指令内部等待与重试，不能当作单次 HTTP 延迟。
`portal.inputState(residentId, messageId)` 已解包 GetInputState 的 result.input_state；返回对象的
`message_id`、`state`、`reply_message_ids` 是顶层字段。直接读取 `input.state`，没有 `input.current`
或第二层 `input.input_state`。`waitForInput` 返回同一形状；先核对精确 message_id 再判断终态。
直接使用其他工具或原始 request 的调用仍需现场采样；中断后的缺失终点不补造。
指令失败日志保留规范错误码、HTTP 状态和安全网络诊断，不保存错误消息或 details 正文。
部署、断线或页面恢复后，继续操作前调用 `await portal.verifyIdentity()`；以当前浏览器身份
重新读取 /api/me，实际身份、build、HTTP 状态及起止耗时追加到 tau-browser-identity.jsonl。
身份校验失败即抛错，不继续业务动作；初始 build 保留，合法变化追加到 build_observations。
框架自动保留原回执，后续采样不能补造此前恢复时的证据。
每个 UI 业务动作使用 `portal.action('static-step-name', async () => {{ /* 操作并等待实际 ready */ }})`，
其他标签页传第三参数 `{{ page: targetPage }}`。助手在原动作开始与结束记录 action、frame/document
及作用域内实际 request ID；没有请求的动作保留空数组。callback 必须包含实际就绪检查，不能在点击后
立即结束计时。同一页面不嵌套或并行 action。作用域内可能包含后台请求，这种关联不证明请求由动作触发，
也不能据此豁免取消或失败；跨页面、Helper 指令请求和 Harness RTT 仍需分别保存原始证据。

测试用例：{planned['case_title']}
自然语言描述：{planned.get('description') or ''}
本条任务：{task_prompt or planned.get('description') or ''}

必须使用已安装的 Playwright 和上述 storage state 操作运行中的真实 Portal。不得读取、打印或
复制浏览器存储状态内容，不得修改应用源码或真实业务数据；只执行本测试用例明确要求的
受控动作。可以利用这个模块 Resident 的历史上下文理解产品，但当前结论必须来自本次操作。
所有证据都必须在本次运行中重新产生，并且只写入上述精确证据目录；最终 evidence
只能填写该目录下由本次运行生成的相对路径。{visual_instruction}
证据只保存支撑结论所需的部分：关键步骤截图，以及与结论相关的页面文本、控件状态或接口
响应摘录。不导出整页网络记录、HAR 或每一步的全量请求与响应；需要网络证据时，只记录相关
请求的方法、路径、状态码和用到的字段。页面主动关闭的事件流（订阅切换、交接、看门狗重连、离开
页面）记录在页面的 `window.PortalEvents.closedStreams()`（url、reason、at）；页面主动取消的
指令请求（读完第一批积压就关闭的 Watch* 流、调用方放弃的调用）也在其中，url 为
`/api/instructions/execute`，另带 instruction。判定预期取消必须逐条核对实际请求 ID、所属 frame/文档以及真实取消或生命周期事实，
不能把路径相同或时间接近当作已证明的因果。未知原因保持 unknown，原失败证据不补造。
所有可修改的测试工程必须放在 fixtures 子目录，通过正常产品入口登记；辅助脚本必须放在 helpers。
用例完成、失败或取消均须清理自建测试对象。先停止本用例的工作，再删除测试条目、取消工程登记，最后删除样例文件及脚本。
在已有工程中创建测试条目后，立即把精确地址写入证据目录 test-fixtures.json 的 wall_items 数组（每项 project、block）；
公共浏览器助手和本用例 Resident 指令中新分配的资源 ID 自动归入本用例生命周期，框架结束时通过 UnregisterResource 调用产品原有删除流程。必须使用公共浏览器助手，让请求携带框架写入 run-context.json 的资源生命周期标识；不要自行创建无关联的浏览器上下文。无需手写 resources 清单；创建理由、类型与回执另存证据文件。旧清单仅用于恢复历史用例，禁止登记输入资源、既有对象或固定团队。
CreateUpload 返回的临时上传句柄不是正式资源 ID；将本用例新建的 upload_id 写入 test-fixtures.json 的 uploads 字符串数组，由框架通过 DeleteUpload 清理。不得把上传句柄混入 resources；没有删除权限时保留清单及错误，不标记清理完成。
Resident 与独立 Lineage 通过统一入口调用各自删除流程；Lineage 内部的组件、Function Block 定义（block_*）、实例、连线不是独立登记资源：通过图编辑清理，或随本用例的临时 Lineage 一起回收，具体 ID 与回执存到独立证据文件。临时 Lineage 的正式 lineage_* ID 自动登记，不要把它内部的 block_* ID 当作资源 ID 注销。
消息、执行回执和已读用户通知属于产品保留的运行历史，不是待注销的正式资源；不得为清理本用例而清空共享收件箱或删除其他历史。
框架会通过普通业务入口再次清理，并在无法停止工作或删除失败时将用例标为 error，保留文件供重试。
fixtures、helpers 与可执行脚本无论是否列入 evidence 都会删除；截图、日志和报告保存在这些目录之外。
用例结束后，没有列入最终 evidence 的其他文件会被删除；需要保留的业务证据仍要列入 evidence。

受测实例可能因正常部署短暂重启。记录重启前后的 build 和时间；相同实例、相同认证身份的
正常 build 更新本身不构成功能失败。等待页面恢复并重新验证身份后，从已经确认的进度继续。
导航、展开和只读查询可以重新执行；创建 Run、Resident、触发 Event 或发送消息等写操作
如未收到明确回执，应先通过正式状态或页面核验是否已成功，不能盲目重发。保留中断与原始
失败证据；恢复后仍无法完成验收时如实判定失败，不以源码、模拟回归或新 build 替代页面验收。

严格分两个阶段执行：第一阶段只从用户视角操作真实页面并记录原始体验，期间禁止读取源码、
SPEC 或实现文档；第二阶段在第一阶段完成后才可读取源码和规范，用于诊断已经观察到的问题。
最终结果必须分别保留 user_experience 与 diagnosis，不能用源码推测替代页面事实。
已受理的 job、input、message 或 invocation 必须持续观察原 ID 直到真实终态；不得以固定
墙钟时长判定失败、停止观察或重新投递。只有用户取消、明确不可恢复错误或任务自身返回
终态时才结束等待，并保留恢复所需 ID 与证据。

完成后，先用自然语言简要报告操作和观察，再以单独一行输出以下 JSON，不要使用 Markdown
代码围栏：
{RESULT_MARKER} {{"status":"passed 或 failed","summary":"结论","user_experience":{{"actions":[],"observations":[],"frictions":[]}},"diagnosis":{{"sources_consulted":[],"findings":[]}},"issues":[],"evidence":[],"visual_review":{{"status":"passed、failed 或 not-applicable","summary":"实际查看截图后的视觉结论"}}}}
没有页面事实支持时必须使用 failed；Agent 对话完成本身不等于测试通过。
"""


def parse_result(message: str, *, require_visual: bool = False) -> dict[str, Any]:
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
    if not isinstance(payload, dict) or payload.get("status") not in {"passed", "failed"}:
        raise ValueError("TAU 结果状态必须是 passed 或 failed")
    evidence = [str(item) for item in payload.get("evidence") or []]
    visual = payload.get("visual_review")
    if not isinstance(visual, dict):
        visual = {"status": "not-applicable", "summary": ""}
    visual_status = str(visual.get("status") or "").strip()
    visual_summary = str(visual.get("summary") or "").strip()
    if require_visual:
        if not evidence:
            raise ValueError("UI 的 AI 真实使用结果必须包含截图证据")
        if visual_status not in {"passed", "failed"} or not visual_summary:
            raise ValueError("UI 的 AI 真实使用结果必须包含明确的 visual_review 判断")
        if visual_status == "failed":
            payload["status"] = "failed"
    return {
        "status": payload["status"],
        "summary": str(payload.get("summary") or "").strip(),
        "issues": [str(item.get("summary") or item.get("title") or item.get("code") or item) if isinstance(item, dict) else str(item) for item in payload.get("issues") or []],
        **({"issue_details": [item for item in payload["issues"] if isinstance(item, dict)]} if any(isinstance(item, dict) for item in payload.get("issues") or []) else {}),
        "evidence": evidence,
        "visual_review": {"status": visual_status or "not-applicable", "summary": visual_summary},
        "user_experience": payload.get("user_experience") if isinstance(payload.get("user_experience"), dict) else {},
        "diagnosis": payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {},
    }


ResidentInvoker = Callable[..., dict[str, Any]]
