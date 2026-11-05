from pathlib import Path
import subprocess


def test_native_send_completes_quota_notification_and_acknowledgement():
    """真实 Chat 封装将发送回执传给额度控制，且不打断成功通知。

    :design: decision_f560803947c6
    """
    root = Path(__file__).resolve().parents[2]
    script = Path(__file__).with_name("javascript") / "home_dialogue_actions.browser.cjs"
    subprocess.run(["node", str(script), "--send-quota-only"], cwd=root, check=True, timeout=60)
