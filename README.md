# The Fourth Institute

**English** | [简体中文](README_CN.md)

**One workspace for your AI agents, their teams, and continuous research.**

The Fourth Institute brings agents from different AI providers into a shared browser interface, where you can direct their work, organize collaboration, and build a research environment that keeps learning over time. Our ambition is to make it possible for anyone to build their own university or research institute, with humans and agents working together.

## Install

### Windows 11 x64 · Preview

**Windows is currently a trial/preview release. Installation, startup, and some features are still being refined and validated.**

[Download the Windows installer](https://github.com/EatonZhou/the-fourth-institute/releases/download/windows-preview-20261021.3/TheFourthInstitute-Setup-x64.exe) · [Release notes and SHA-256 checksum](https://github.com/EatonZhou/the-fourth-institute/releases/tag/windows-preview-20261021.3)

Double-click `TheFourthInstitute-Setup-x64.exe` and choose a new, empty installation folder. Setup lets you choose whether to include the Lenia example, then starts Portal and opens your browser. Python, PostgreSQL, NATS, and Node.js are included. First use requires internet access for core Python packages. Optional tools such as TeX are not installed automatically; missing tools affect only the features that use them. Later, use the desktop icon to open Portal.

The installer is unsigned and does not support in-place upgrades of existing instances. See the [Windows instructions and verification limits](code/scripts/windows/README.md).

### macOS 15+ · Preview

Apple Silicon (arm64) and Intel (x64) installers are published to
[macOS Preview Releases](https://github.com/EatonZhou/the-fourth-institute/releases?q=macos-preview&expanded=true)
after native build verification. Open the DMG, drag the app into Applications, and choose
whether to include the Lenia example on first launch. Python, core Python packages and native
services are bundled; local setup does not require internet access. This preview is unsigned,
not notarized, and does not support in-place upgrades. See the [macOS setup guide](code/scripts/macos/README.md).

### Linux

Requires Python 3.12 with venv support. Clone the repository and prepare the installation bundle:

```bash
git clone https://github.com/EatonZhou/the-fourth-institute.git
cd the-fourth-institute
python3 prepare_install.py
```

`prepare_install.py` verifies the included example seed and assembles `.build/example_instance/engineering/` for installation. It leaves running instance data unchanged.

Follow [the installation guide](INSTALL.md) to create `target.json` for your instance, then install:

```bash
python3 code/scripts/tfi_release_install.py install --target target.json --manifest .build/example_instance/engineering/manifest.json
```

The installer can reuse configured dependencies or prepare supported dependencies for the instance. Downloading missing packages requires internet access. The guide covers initialization, startup, service status, logs, and upgrades.

You can also ask an AI agent on your machine to read [the installation guide](INSTALL.md), prepare the configuration, and help complete the installation.

## Quick Start

### 1. Open Portal and Sign In

After installation and service startup, open Portal at:

- **On the installation host:** `http://127.0.0.1:<web-port>/`, for example `http://127.0.0.1:28086/`.
- **From another device on the same LAN:** `http://<host-ip>:<web-port>/`, for example `http://192.168.1.100:28086/`.

Use the `ports.web` value from your `target.json`; `28086` is the port in the installation guide's example. On Linux, run `hostname -I` on the installation host and choose its LAN IP address.

Access through the host IP requires setting `"bind": "0.0.0.0"` in `target.json` before installation and allowing the Web port through the host's firewall. The default `"bind": "127.0.0.1"` allows access only from the installation host. `0.0.0.0` is a listening address; enter the host's actual IP in the browser.

The Windows launcher opens the administrator registration page on first use. For a command-line installation, follow the guide's `first-admin` step and open the generated invitation link to set your password.

### 2. Connect an AI Backend

In Portal's execution settings, register your AI provider account or model API, prepare the required runtime, test a model response, and select a default AI. Existing programs and accounts on your machine can be reused where supported. Your local AI agent can help with setup.

### 3. Give an Agent Its First Task

Open a Resident conversation or choose an included team, add your materials, and give it a concrete task. For example: “Read these papers, compare their experimental methods, and write a summary with source links.”

Start with a small task whose results you can check. You can then add members, assign different backends, and organize longer workflows.

## What You Can Do

### 1. Operate Your AI Agents from One App

Use a common browser interface for **Codex, Claude Code, DeepSeek Harness (DSH), OpenCode, and Kimi Code**. Think of it as a shared graphical interface for agents you would otherwise operate through separate terminal applications: manage conversations, accounts, execution status, and usage in one place.

You can also connect cloud services or self-hosted model endpoints compatible with the **OpenAI Chat Completions API**, using an API base URL, API key, and model configuration. API connections can supply models to compatible agent backends.

**One Person Managing an Agent Team**

![One Person Managing an Agent Team](docs/images/one-person-agent-team-en.png "One Person Managing an Agent Team")

### 2. Organize Teams across AI Providers

Agents using different backends can work on the same project, share resources, and exchange results. For example, you could assign implementation to a Codex agent, review to a Claude Code agent, and experimental analysis to a DeepSeek Harness agent.

- **Residents** give agents persistent identities, workspaces, and records across sessions.
- **Lineages** organize members and connect their work through events and task handoffs, including branches that explore different approaches.
- **Families** give closely collaborating members a shared entry point, so you can work with a team as a whole.

People guide the work through conversations and the visual workspace. Agents use HATG ISA (the Human-Agent Trajectory Graph instruction set) to access resources and coordinate work. Shared papers, knowledge entries, code, and workspaces let findings carry over into the next task.

**Human-AI Collaboration Workspace**

![Human-AI Collaboration Workspace](docs/images/human-ai-collaboration-en.png "Human-AI Collaboration Workspace")

### 3. Keep a Research Team Working from Idea to Paper

Organize a continuous research workflow: propose ideas, review literature, develop hypotheses, write code, run experiments, analyze results, and produce papers. With the necessary models, tools, data, and compute available, agents can carry out this process automatically and iterate on the findings without a person coordinating every step.

Teams can explore several directions in parallel and use new results to decide what to investigate next. You can join at any point to adjust goals, review evidence, or redirect the work. Discussions, executions, and tool calls remain available for inspection; scientific reliability and research value still require validation.

The included cellular automata project illustrates this workflow. Its status as an AI-generated demonstration is explained [below](#ai-generated-content-statement).

### 4. Bring Your Agents into a Larger Research Network

Researchers can organize shared projects within a station, where humans and external agents participate as Users alongside the station's Residents. This gives a team a place to combine its questions, knowledge, tools, and experimental resources.

Our longer-term goal is an open network of research stations: bring your own agents into a larger research project, share findings between teams, and collaborate across institutions. Public agent uploads, discovery across stations, and automated collaboration between stations remain future directions.

We encourage you to build your own research station, open it to collaborators with appropriate access controls, and share questions that other people's agents can help explore—an agent-native research institute, or an agent playground :P

## Working at Scale: Tokens and Disk Space

In our own use, **one person has coordinated hundreds of agents working together for days**. In one high-load hour, our recorded token usage exceeded **2B (2 billion) tokens**. These figures describe our experience; scale and consumption vary with the models, context sizes, concurrency, and tasks.

Long-running teams can also consume substantial disk space. Conversation records, tool output, experimental data, and intermediate artifacts accumulate and can quickly fill a machine's disk. Start with a small team, monitor token usage and free disk space, and expand within your available resources. Storage management, reducing duplicate data, and controlling running costs are areas for further optimization.

## Contributing

Issues and pull requests are welcome, including:

- Research questions suited to collaboration between humans and agents, with experiments and reproduction records that others can inspect.
- Agent configurations, collaboration templates, tools, and model backend adapters.
- Improvements to installation, documentation, and examples, along with reports of issues encountered in practice.
- Ways for stations to share knowledge, invite collaborators, and exchange research findings.

Share only materials and agent configurations you have permission to distribute. Remove personal credentials, private account settings, and data that should not be public.

## AI-Generated Content Statement

All content in this open-source project is AI-generated. You may encounter unexpected problems during installation or use. If installation fails, you can use your own AI agent to investigate and fix the issue.

The software includes an agent team dedicated to developing the software itself. All users can fix issues, customize features to suit their needs, and explore future directions for the project.

The included automated research project on cellular automata is provided solely as a demonstration. Its entire process and results are AI-generated. The resulting papers and experimental findings have no academic value.
