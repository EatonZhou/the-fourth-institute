#!/usr/bin/env python3
"""Manage bearer tokens for Portal agent APIs."""
from __future__ import annotations

import argparse
import sys

from .agent_token_auth import (
    generate_token,
    hash_token,
    get_agent_token_store,
    token_lookup,
)


def set_agent(agent_id: str) -> int:
    agent_id = agent_id.strip()
    if not agent_id:
        print("agent_id required", file=sys.stderr)
        return 2
    token = generate_token()
    store = get_agent_token_store()
    with store.transaction(agent_id):
        previous = store.get(agent_id) or {}
        store.put(agent_id, {
            **previous,
            "token_hash": hash_token(token),
            "token_lookup": token_lookup(token),
            "enabled": True,
        })
    print(f"agent_id: {agent_id}")
    print(f"token: {token}")
    print("Store this token in the agent VM; it will not be shown again.")
    return 0


def disable_agent(agent_id: str) -> int:
    store = get_agent_token_store()
    with store.transaction(agent_id):
        config = store.get(agent_id)
        if config is None:
            print(f"agent not found: {agent_id}", file=sys.stderr)
            return 1
        config["enabled"] = False
        store.put(agent_id, config)
    print(f"disabled: {agent_id}")
    return 0


def list_agents() -> int:
    config = get_agent_token_store().list()
    for agent_id in sorted(config):
        cfg = config[agent_id]
        enabled = "enabled" if cfg.get("enabled") is not False else "disabled"
        print(f"{agent_id}\t{enabled}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Portal agent bearer tokens.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    set_parser = sub.add_parser("set", help="Generate and store a new token for an agent.")
    set_parser.add_argument("agent_id")

    disable_parser = sub.add_parser("disable", help="Disable an agent token.")
    disable_parser.add_argument("agent_id")

    sub.add_parser("list", help="List configured agents without showing tokens.")

    args = parser.parse_args()
    if args.cmd == "set":
        return set_agent(args.agent_id)
    if args.cmd == "disable":
        return disable_agent(args.agent_id)
    if args.cmd == "list":
        return list_agents()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
