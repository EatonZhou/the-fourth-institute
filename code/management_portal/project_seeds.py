from __future__ import annotations


def project_seed_documents(project_id: str, description: str, language: str = "zh-CN") -> dict[str, str]:
    if language == "en":
        description_text = description or "[To be completed]"
        return {
            "agent_port/boot.md": (
                f"# {project_id} project guide\n\n"
                f"## Project overview\n{description_text}\n\n"
                "## Current capabilities\n- [To be completed]\n\n"
                "## Usage constraints\n- [To be completed]\n"
            ),
            "agent_port/index.md": (
                "# agent_port index\n\n## Entry points\n"
                "- `boot.md`: project guide\n"
                "- `skills/`: project skills\n"
                "- `tools/`: project tools\n"
            ),
            "summary.md": (
                f"# {project_id}\n\n## 1. Project goals\n{description_text}\n\n"
                "## 2. Directory structure\n"
                "- `agent_port/`\n- `agents/`\n- `knowledge_base/`\n- `src/`\n- `to_archive/`\n\n"
                "## 3. Current status\nactive\n"
            ),
        }
    description_text = description or "[待补充]"
    return {
        "agent_port/boot.md": (
            f"# {project_id} 项目指南\n\n"
            f"## 项目概述\n{description_text}\n\n"
            "## 当前能力\n- [待补充]\n\n"
            "## 使用约束\n- [待补充]\n"
        ),
        "agent_port/index.md": (
            "# agent_port 索引\n\n"
            "## 入口说明\n"
            "- `boot.md`：项目引导文件\n"
            "- `skills/`：项目技能\n"
            "- `tools/`：项目工具\n"
        ),
        "summary.md": (
            f"# {project_id}\n\n"
            f"## 1. 项目目标\n{description_text}\n\n"
            "## 2. 目录结构\n"
            "- `agent_port/`\n"
            "- `agents/`\n"
            "- `knowledge_base/`\n"
            "- `src/`\n"
            "- `to_archive/`\n\n"
            "## 3. 当前状态\nactive\n"
        ),
    }
