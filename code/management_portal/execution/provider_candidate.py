"""Run the staged provider's wire contract in a disposable account before activation."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

from management_common.process_runtime import run_process
from .provider_preparation import PreparationError, phase


class CandidateCheckError(PreparationError):
    descriptions = {
        "test_profile": "请为更新服务配置已登录的 Antigravity 专用测试账号目录 AGY_TEST_PROFILE。",
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
    from .provider_updates import ROOT, venv_python

    with phase("interface_validation"), tempfile.TemporaryDirectory(prefix="tfi-probe-") as home:
        # In particular, do not forward live account credentials or database URLs.
        env = {k: os.environ[k] for k in ("PATH", "SystemRoot", "WINDIR", "PATHEXT",
               "COMSPEC", "PORTAL_INSTANCE_ROOT", "LANG") if k in os.environ}
        env.update(HOME=home, USERPROFILE=home, XDG_CONFIG_HOME=home + "/config",
                   XDG_DATA_HOME=home + "/data", XDG_CACHE_HOME=home + "/cache",
                   XDG_STATE_HOME=home + "/state", APPDATA=home, LOCALAPPDATA=home,
                   CODEX_HOME=home, CLAUDE_CONFIG_DIR=home, GEMINI_HOME=home, KIMI_CODE_HOME=home,
                   PYTHONUTF8="1", PYTHONNOUSERSITE="1")
        # Seed history using the running SDK, then resume it using the candidate.
        # For initial installations, also exercise a cold restart of the candidate.
        previous = active.resolve() if active.exists() else root
        work = Path(home, "work")
        work.mkdir()
        versions = [previous, root]
        if provider == "antigravity":
            source = os.environ.get("AGY_TEST_PROFILE")
            if source:
                profile = Path(home, "antigravity-acp")
                profile.mkdir(mode=0o700)
                for name in ("antigravity-oauth-token", "acp_token.json"):
                    token = Path(source, profile.name, name)
                    if token.is_file():
                        shutil.copyfile(token, profile / name)
                        (profile / name).chmod(0o600)
                if not any(profile.iterdir()):
                    raise CandidateCheckError("test_profile", FileNotFoundError())
                env["TFI_PROBE_AUTHENTICATED"] = "1"
                from management_portal.antigravity_runtime import binary_path
                if not binary_path(previous).is_file():
                    versions = [root, root]
            elif active.exists() and previous != root.resolve():
                raise CandidateCheckError("test_profile", FileNotFoundError())
            else:
                # A first-time install can bootstrap the CLI before account enrollment.
                versions = [root]
        for index, version in enumerate(versions):
            interpreter = venv_python(version) if provider == "claude" else sys.executable
            command = [str(interpreter), str(ROOT / "scripts/provider_candidate_probe.py"),
                       provider, str(version.resolve(strict=True))]
            try:
                run_process(command, env=env, cwd=work, timeout=240 if provider == "antigravity" else 120,
                            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as exc:
                check = "baseline" if index == 0 and len(versions) > 1 else "candidate"
                raise CandidateCheckError(check, exc) from exc
