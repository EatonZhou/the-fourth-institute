"""Native AI connection guidance and recovery reuse the registration protocol."""
from pathlib import Path
import subprocess


def test_backend_registration_guidance():
    """Subscription and API setup, including safe retry after partial success.

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") /
                               "backend_registration_guidance.cjs")], cwd=root, check=True)


def test_provider_onboarding_checks_before_completion_and_recovers_on_failure():
    """接入 Provider 时先测试再完成，准备运行环境只做一次，失败时给出下一步和只读日志

    检查通过后以检查执行 ID 调用 UpdateBackendRole 并关闭对话框，按打开时的版本保存；准备运行环境时显示进度，后台轮询不替换当前状态、不重装、不打断正在输入的模型草稿；
    检查未通过时不保存，提示更换 API Key，详细日志只读、按纯文本显示且可收起；准备失败不循环重试，重试时不显示旧日志。

    :module: execution.providers
    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["node", str(Path(__file__).with_name("javascript") / "provider_onboarding.cjs")],
                            cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
