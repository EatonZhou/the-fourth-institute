"""The admin page probes a Model API's protocols and moves the Codex default route."""
from pathlib import Path
import subprocess


def test_model_api_probe_and_default_route_ui():
    """检测结果勾选模型 API 的协议，Agent 运行器随之变化；Codex 默认路线有标记，可设为另一个启用的账号

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / "model_api_probe.cjs")],
                   cwd=root, check=True)
