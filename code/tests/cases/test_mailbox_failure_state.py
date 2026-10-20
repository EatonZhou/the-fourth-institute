"""Mailbox failure contracts exercised through the production JS store and view.

:design: decision_18abbdef3da3
"""
from pathlib import Path
import subprocess


def _run_contract(name):
    source = Path(__file__).with_name("javascript") / "mailbox_failure_state.cjs"
    result = subprocess.run(
        ["node", "--test", f"--test-name-pattern=^{name}$", str(source)],
        capture_output=True, text=True, timeout=30,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"# Subtest: {name}\n" in result.stdout, "The requested JS contract did not run"


def test_initial_failure_recovers_on_retry():
    """Mailbox初次加载失败的提示与刷新恢复

    Stable case test_5e7d4d705fad.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("initial Mailbox failure is actionable and retry renders the recovered entries")


def test_failed_read_rolls_back_and_retries():
    """Mailbox标记已读失败的乐观回滚与重试

    Stable case test_5a563a8c221f.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("failed Mailbox read rolls back optimistic state and can be retried")


def test_list_stops_at_the_oldest_loaded_resident_page():
    """Mailbox只显示到已加载的Resident分页为止

    第一页带回全部通知、提及和 Chatroom，Resident 活动分页加载。还有下一页时，
    列表只显示到已加载的最旧 Resident 活动为止；更旧的通知在“加载更多”后按时间
    排在后续 Resident 之后，和 GetMailbox（user_id）的顺序一致。

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    _run_contract("Mailbox shows entries only as far as the loaded Resident pages reach")
