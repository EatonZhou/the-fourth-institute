"""Connected AI groups one API's runners while preserving account identity and controls."""
from pathlib import Path
import subprocess


def test_connected_ai_groups_shared_api_accounts():
    """共享同一 API 的执行账号折叠到一个连接，展开后显示运行器、账号、状态和独立操作。

    :design: decision_7956d7cb4bde
    """
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["node", str(Path(__file__).with_name("javascript") / "backend_connected_ai.cjs")], cwd=root, check=True)
