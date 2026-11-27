"""Gate activation on the local upgrade contract and one upstream turn on the staged runtime."""
from pathlib import Path
import logging
import os
import subprocess
import sys
import tempfile

from management_common.process_runtime import run_process
from . import provider_probe_account
from .provider_preparation import PreparationError, phase


ACCEPTANCE_SECONDS = 90
LOGGER = logging.getLogger(__name__)


class CandidateCheckError(PreparationError):
    descriptions = {
        "test_profile": "无法准备已登记的可用验收账号，请检查登录状态、额度或 API 配置。",
        "live_account": "候选版本的真账号测试未取得预期的最终回复。",
        "baseline": "当前 SDK 未能完成测试会话的创建。",
        "candidate": "候选 SDK 未通过接口或会话接续检查。",
    }

    def __init__(self, check, cause):
        super().__init__("interface_validation", cause)
        self.check = check
        self.args = (self.descriptions[check],)

    def public(self):
        result = super().public()
        result["diagnostic"]["check"] = self.check
        return result


def validate(provider, root, active):
    """Register before starting either SDK. Only the independent supervisor owns the deadline."""
    from management_portal.society_service.transport import SocietyServiceClient

    supervisor = SocietyServiceClient(timeout=5).lineage
    with phase("interface_validation"):
        attempt = supervisor.register_provider_validation(provider, root.name, ACCEPTANCE_SECONDS)

    def set_stage(stage):
        try:
            supervisor.set_validation_stage(attempt, stage)
        except Exception:
            LOGGER.exception("Could not report %s stage for validation %s", stage, attempt)

    try:
        _validate(provider, root, active, set_stage)
    finally:
        try:
            supervisor.finish_validation(attempt)
        except Exception:
            LOGGER.exception("Could not report completion of validation %s", attempt)


def _environment(home):
    env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "PATHEXT",
           "COMSPEC", "PORTAL_INSTANCE_ROOT", "LANG") if k in os.environ}
    env.update(HOME=home, USERPROFILE=home, XDG_CONFIG_HOME=home + "/config",
               XDG_DATA_HOME=home + "/data", XDG_CACHE_HOME=home + "/cache",
               XDG_STATE_HOME=home + "/state", APPDATA=home, LOCALAPPDATA=home,
               CODEX_HOME=home, CLAUDE_CONFIG_DIR=home, GEMINI_HOME=home, KIMI_CODE_HOME=home,
               PYTHONUTF8="1", PYTHONNOUSERSITE="1")
    return env


def _run(provider, version, script, env, work, check):
    from .provider_updates import ROOT, ClaudeRuntime, venv_python
    interpreter = venv_python(ClaudeRuntime().selected_sdk(version)) if provider == "claude" else sys.executable
    command = [str(interpreter), str(ROOT / "scripts" / script), provider, str(version.resolve(strict=True))]
    try:
        run_process(command, env=env, cwd=work, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CandidateCheckError(check, exc) from exc


def _validate(provider, root, active, set_stage):
    previous = active.resolve() if active.exists() else root
    if provider == "antigravity":
        from management_portal.antigravity_runtime import binary_path
        if not binary_path(previous).is_file():
            previous = root

    with phase("interface_validation"), tempfile.TemporaryDirectory(prefix="tfi-probe-") as home:
        env = _environment(home)
        work = Path(home, "work")
        work.mkdir()
        for check, version in (("baseline", previous), ("candidate", root)):
            set_stage(check)
            _run(provider, version, "provider_candidate_probe.py", env, work, check)

    with phase("interface_validation"), tempfile.TemporaryDirectory(prefix="tfi-live-") as home:
        set_stage("live_account")
        env = _environment(home)
        work = Path(home, "work")
        work.mkdir()
        try:
            provider_probe_account.prepare(provider, env)
        except Exception as exc:
            raise CandidateCheckError("test_profile", exc) from exc
        _run(provider, root, "provider_candidate_live.py", env, work, "live_account")
