#!/usr/bin/env python3
"""快速检查 Skill 的 frontmatter 和名称。"""

import re
import sys
from pathlib import Path

import yaml

MAX_SKILL_NAME_LENGTH = 64


def validate_skill(skill_path):
    """检查一个 Skill 目录的基本结构。"""
    skill_path = Path(skill_path)

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as exc:
        return False, f"Invalid YAML in frontmatter: {exc}"

    allowed_properties = {
        "name",
        "description",
        "license",
        "allowed-tools",
        "metadata",
    }

    unexpected_keys = set(frontmatter.keys()) - allowed_properties
    if unexpected_keys:
        allowed = ", ".join(sorted(allowed_properties))
        unexpected = ", ".join(sorted(unexpected_keys))
        return (
            False,
            "Unexpected key(s) in SKILL.md frontmatter: "
            f"{unexpected}. Allowed properties are: {allowed}",
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if not name:
        return False, "Name must not be empty"
    if name != skill_path.name:
        return False, "Name must match the Skill directory"
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict) or not re.fullmatch(
        r"skill_[0-9a-f]{12}", str(metadata.get("id") or "")
    ):
        return False, "metadata.id must be a stable skill_<12 lowercase hex> ID"
    if name:
        if not re.match(r"^[a-z0-9-]+$", name):
            return (
                False,
                f"Name '{name}' should be hyphen-case "
                "(lowercase letters, digits, and hyphens only)",
            )
        if name.startswith("-") or name.endswith("-") or "--" in name:
            return (
                False,
                f"Name '{name}' cannot start/end with hyphen "
                "or contain consecutive hyphens",
            )
        if len(name) > MAX_SKILL_NAME_LENGTH:
            return (
                False,
                f"Name is too long ({len(name)} characters). "
                f"Maximum is {MAX_SKILL_NAME_LENGTH} characters.",
            )

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return (
            False,
            f"Description must be a string, got {type(description).__name__}",
        )
    description = description.strip()
    if not description:
        return False, "Description must not be empty"
    if description:
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)"
        if len(description) > 1024:
            return (
                False,
                f"Description is too long ({len(description)} characters). "
                "Maximum is 1024 characters.",
            )

    for name in ("agents", "reference", "references", "docs"):
        if (skill_path / name).exists():
            return False, f"Portal Skill documents must be flat; remove {name}/"
    return True, "Skill structure is valid; verify workflow and ID uniqueness separately."


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
