"""Mailbox presentation demand bounds reads without stopping business work.

:module: resident.conversation
"""
from pathlib import Path
import subprocess


def test_node_mailbox_filters_events_and_bounds_cancellable_details():
    source = Path(__file__).with_name('javascript') / 'mailbox_read_budget.cjs'
    result = subprocess.run(['node', str(source)], capture_output=True, text=True, timeout=20)
    print(result.stdout, end='')
    assert result.returncode == 0, result.stdout + result.stderr


def test_closed_global_mailbox_drops_full_state_demand_but_keeps_badge():
    source = Path(__file__).with_name('javascript') / 'message_state_mailbox.cjs'
    result = subprocess.run(['node', str(source), 'closed-state-demand'], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_global_mailbox_reacquired_demand_calibrates_once_and_shares_chat_read():
    source = Path(__file__).with_name('javascript') / 'message_state_mailbox.cjs'
    result = subprocess.run(['node', str(source), 'state-demand-recovery'], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
