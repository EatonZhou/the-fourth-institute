"""Export the execution protocol's required rule files, independently of user Skills."""
from pathlib import Path
import re

from management_portal.agent_skill_context import PORTAL_ROLE_FILES, materialize_portal_skills

CORE_SUPPORT_FILES = ('SKILL.md', 'scripts/resource_id.py')
SYSTEM_SKILL = 'tfi-agent-rule'
IDENTITY_SKILL = 'tfi-unified-id'


def canonical_resources():
    """The shipped rule manifests own their identities and canonical folders."""
    import yaml
    root = Path(__file__).resolve().parents[2] / 'core_skills'
    resources = {}
    for name in (SYSTEM_SKILL, IDENTITY_SKILL):
        text = (root / name / 'SKILL.md').read_text(encoding='utf-8')
        metadata = yaml.safe_load(text.split('---', 2)[1])
        resources[metadata['metadata']['id']] = root / name
    return resources

def export_core_skills(destination, *, source_root=None, source_usernames=()):
    from .engineering import checksum, copy_content

    repository = Path(__file__).resolve().parents[2]
    source_root = Path(source_root) if source_root is not None else repository / "core_skills"
    source = source_root / SYSTEM_SKILL
    target = Path(destination) / "data/share_root/Skills" / SYSTEM_SKILL
    mappings = ((str(source_root / SYSTEM_SKILL), "${share_root}/Skills/" + SYSTEM_SKILL),
                (str(source_root / IDENTITY_SKILL), "${share_root}/Skills/" + IDENTITY_SKILL),
                (str(repository), "${code_root}"))
    pending = {"SKILL.md", *PORTAL_ROLE_FILES.values()}
    files = []
    seen = set()
    while pending:
        name = min(pending)
        pending.remove(name)
        if name in seen:
            continue
        seen.add(name)
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Required Portal core Skill file is unavailable: " + name)
        text = path.read_text(encoding="utf-8")
        # Only local Markdown dependencies of these rules, never an entire Skill tree.
        pending.update(re.findall(r"\./([A-Za-z0-9_-]+\.md)", text))
        output = target / name
        copy_content(path, output, selected=set(), anonymize=False,
                     mappings=mappings,
                     source_usernames=source_usernames)
        files.append({"path": output.relative_to(destination).as_posix(),
                      "sha256": checksum(output), "size": output.stat().st_size})
    for role in ("general", *PORTAL_ROLE_FILES):
        materialize_portal_skills(
            role, skill_root=str(target.parent), system_skill_name=SYSTEM_SKILL)
    # The common rule requires the unified-ID Skill for resource addresses.
    # Its CLI is part of that rule's executable interface, not a user Workspace.
    for relative in CORE_SUPPORT_FILES:
        path = source_root / IDENTITY_SKILL / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Required Portal support Skill file is unavailable: " + relative)
        output = target.parent / IDENTITY_SKILL / relative
        copy_content(path, output, selected=set(), anonymize=False,
                     mappings=mappings,
                     source_usernames=source_usernames)
        files.append({"path": output.relative_to(destination).as_posix(),
                      "sha256": checksum(output), "size": output.stat().st_size})
    return {"reason": "Required by Portal execution before model invocation",
            "files": files, "roles": ["general", *PORTAL_ROLE_FILES]}
