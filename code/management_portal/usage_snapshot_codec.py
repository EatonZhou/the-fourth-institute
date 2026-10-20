"""Lossless JSON dictionary encoding for repeated usage scopes and metadata."""
from __future__ import annotations

import json


FORMAT = "usage-dictionary-v1"


def encode(key: str, entry: dict) -> dict:
    nodes, seen = [], {}

    def intern(value):
        if isinstance(value, dict):
            node = ["dict"]
            for name, child in value.items():
                node.extend((intern(name), intern(child)))
            signature = tuple(node)
        elif isinstance(value, list):
            node = ["list", *(intern(child) for child in value)]
            signature = tuple(node)
        else:
            node = value
            signature = ("scalar", type(value).__name__, json.dumps(value, ensure_ascii=False))
        if signature not in seen:
            seen[signature] = len(nodes)
            nodes.append(node)
        return seen[signature]

    root = intern(entry)
    return {"format": FORMAT, "key": key, "root": root, "nodes": nodes}


def decode(document: dict) -> dict:
    if document.get("format") != FORMAT:
        raise ValueError("Unknown usage snapshot format")
    nodes = document["nodes"]

    def expand(index, limit):
        if type(index) is not int or not 0 <= index < limit:
            raise ValueError("Invalid usage snapshot reference")
        node = nodes[index]
        if not isinstance(node, list):
            if isinstance(node, dict):
                raise ValueError("Invalid usage snapshot scalar")
            return node
        # Expand each occurrence separately so variants cannot share mutable rows.
        if node and node[0] == "list":
            return [expand(ref, index) for ref in node[1:]]
        if node and node[0] == "dict" and len(node) % 2 == 1:
            return {expand(node[i], index): expand(node[i + 1], index) for i in range(1, len(node), 2)}
        raise ValueError("Invalid usage snapshot node")

    entry = expand(document["root"], len(nodes))
    if not isinstance(entry, dict):
        raise ValueError("Invalid usage snapshot entry")
    return entry
