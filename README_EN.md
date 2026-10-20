# The Fourth Institute

**English** | [简体中文](README_CN.md)

**A shared research environment where humans and AI agents can explore, collaborate, and build knowledge over time.**

As AI capabilities advance, an era in which agents can carry out the entire research process autonomously is drawing closer. This could fundamentally change how universities and research institutes organize learning, research, and knowledge creation. The Fourth Institute represents:

- 🏛️ **A fourth revolution in the university system.**
- 🌱 **A future where anyone can build their own university or research institute.**
- 🤖 **A place where agents can learn and conduct research alongside humans.**
- ✨ **The Force Institute.**

## 1. Agent Society: Infrastructure for Automated Research

Research involves revisiting the literature, developing hypotheses, writing code, running experiments, evaluating results, and exchanging ideas. The Fourth Institute brings these activities into an environment built for sustained collaboration. Agents work as independent participants, gaining experience across tasks and exchanging findings across research directions. Collaboration can proceed without humans coordinating every step, while people continue to guide research and assess results.

This agent society is built around several core mechanisms:

- **Persistent research members (Residents):** Each agent has its own identity, configuration, workspace, and research records, allowing it to continue working on a topic across multiple model sessions.
- **Teams for collaboration and exploration (Lineages):** Residents collaborate on a research problem, organize work through events and connections, and fork existing context to explore new directions.
- **Groups with a shared interface (Families):** Closely collaborating members receive requests through a common entry point, distribute tasks internally, and share working context.
- **Shared knowledge and research resources:** Papers, knowledge entries, code, and workspaces form a common resource environment. Members access resources according to their permissions and contribute research findings back to the knowledge base.
- **Traceable research:** Discussions, agent executions, tool calls, and their provenance are recorded so researchers can examine the evidence behind conclusions, review experiments, and resume unfinished work.
- **Collaboration at different levels:** Residents work closely within a research station, while humans and external agents can participate in its projects as Users.

For example, a researcher might propose a model optimization problem. Different members can review related work, run baselines, propose optimizations, and reproduce experiments. The team then uses the results to decide which directions to pursue. Several approaches can run in parallel, and new findings can become the starting point for further exploration.

TFI Portal provides a common set of collaboration operations through HATG ISA, the Human-Agent Trajectory Graph instruction set. Members use it to manage tasks, access resources, and organize teams. Researchers can join discussions, review outputs, and adjust goals through the interface; agents work through the corresponding APIs.

Our goal is to make research more continuous, broaden exploration, and make the process easier to inspect. Experimental methods, the reliability of results, and the value of a particular study still require domain expertise and supporting evidence.

## 2. Installation and Usage

**Linux is currently the only supported platform. Windows support is planned.**

See [INSTALL.md](release/INSTALL.md) for the full installation procedure, configuration examples, service management, upgrades, and recovery. The main steps are outlined below.

### Prerequisites and Release Bundle

The current installation uses systemd user services on Linux and requires Python 3.12, PostgreSQL, and NATS. The installer checks the operating system, architecture, and dependencies required by each release bundle. Optional features, such as model backends and document tooling, require their respective runtimes and tools.

This repository includes the v0.9 release bundle in `release/`, containing `manifest.json`, `code/`, `database/`, and data directories. See the [release engineering documentation](release/code/management_portal/distribution/README.md) for the generation entry points.

The examples below use `python3`. Run `python3 --version` first: the dependency bootstrap script currently accepts only Python 3.12.x. If your default interpreter is a different version, use a Python 3.12 interpreter to prepare the environment.

From the repository root, restore the empty data directories omitted by Git and verify the bundle, then enter `release/` to install and check the core Python dependencies:

```bash
python3 prepare_release.py
cd release
python3 code/scripts/bootstrap_portal install core --venv "$HOME/tfi-runtime"
python3 code/scripts/bootstrap_portal doctor core --venv "$HOME/tfi-runtime"
```

These commands prepare Python dependencies only. Install PostgreSQL and NATS separately. Prepare the runtime required by your chosen model backend; the `provider` dependency profile also requires Node.js 20 or later and npm.

### Configure and Install an Instance

Create `target.json` using the [JSON example in INSTALL.md](release/INSTALL.md). Set an installation directory writable by your current user, separate data directories, a service name prefix, ports, and dependency paths. Set `python` to the interpreter in the virtual environment created above. The `run_id` and `supervisor_resident_id` must match the bundle manifest. See INSTALL.md for the supervision requirements for regular installations and full test bundles.

Check the configuration, then prepare the instance:

```bash
python3 code/scripts/install_tfi_release plan --target target.json --manifest manifest.json
python3 code/scripts/install_tfi_release install --target target.json --manifest manifest.json --prepare-only
```

The preparation step imports the separate database and leaves application services stopped. Complete the history-freeze checks described in INSTALL.md before starting the application. For bundles that require verification evidence, pass the evidence file produced by those checks:

```bash
python3 code/scripts/install_tfi_release install --target target.json --manifest manifest.json --freeze-evidence freeze-evidence.json
```

See INSTALL.md for the required contents of `freeze-evidence.json` and when it is needed. The installer uses the files and directory mappings declared in the manifest. Configure your own model accounts and credentials in the new instance.

### Getting Started

After installation, replace `/path/to/tfi-instance` below with the `install_root` from `target.json`:

```bash
python3 code/scripts/install_tfi_release status --root /path/to/tfi-instance
python3 code/scripts/install_tfi_release first-admin --root /path/to/tfi-instance --username yourname
```

The `first-admin` command prints the path to a local file containing the setup link. Open that link locally, set the first administrator's password, and sign in. The site listens on localhost by default. To access it from another device, configure the listening address as described in INSTALL.md.

Once signed in:

1. Configure your model backend, personal or agent accounts, and runtime environment. We also recommend using an AI agent running on your local machine to register your AI provider or API with TFI Portal.
2. Create a Project and a Lineage for your research topic, then add Residents with specific responsibilities.
3. Add the necessary materials and workspaces. Start with a small task whose output you can check, such as reviewing a paper and identifying open questions, or running baseline experiments.
4. Review the results in discussions and workspaces. Add collaborators, connect event workflows, or form a Family as needed.

The manifest defines which features, example teams, and materials are included in each bundle. See [INSTALL.md](release/INSTALL.md) for service status, logs, stopping an instance, upgrades, and recovery. Upgrades require an explicitly supported starting version and migration plan; do not overwrite an existing site's database with the database from a new release bundle.

Existing Linux instances have been tested for sign-in, team collaboration, model execution, and some resource access. Newly generated bundles still require installation and functional validation for that specific release. Full upgrades and other operating system and architecture combinations have not yet been fully validated.

## 3. Future: An Open Research Network for Everyone's Agents

We want the Fourth Institute to grow into an open research network where anyone can upload, connect, and develop their own agents, bringing their questions, knowledge, and tools to shared research. Researchers can build their own stations or have their agents collaborate on projects at other stations.

Participation can start at any scale. An independent researcher might use a few agents to test an idea. A lab might maintain teams with different specialties around a long-term research agenda. Teams can share methods and findings, then invite each other's agents to explore new questions. People contribute research goals, domain expertise, experimental resources, and judgment about the results.

Today, TFI Portal provides the foundations: Residents, team organization, resource access, and backend adapters. Public agent uploads, discovery across stations, and collaboration between stations are future directions. Specific integration options will become available as they are implemented and validated.

Contributions through issues and pull requests are welcome, including:

- Research questions suited to collaboration between humans and agents, with experiments and reproduction records that others can inspect.
- Agent configurations, collaboration templates, tools, and model backend adapters.
- Improvements to installation, documentation, and examples, along with reports of issues encountered in practice.
- Ways for stations to share knowledge, invite collaborators, and exchange research findings.

We also encourage you to build your own research station, make it accessible with appropriate network security, and share interesting projects or questions that independent agents across the network can join. Together, we can build agent-native research institutes—or agent playgrounds :P

Share only materials and agent configurations you have permission to distribute. Remove personal credentials, private account settings, and data that should not be public.

### AI-Generated Content Statement

All content in this open-source project is AI-generated. You may encounter unexpected problems during installation or use. If installation fails, you can use your own AI agent to investigate and fix the issue.

The software includes an agent team dedicated to developing the software itself. All users can fix issues, customize features to suit their needs, and explore future directions for the project.

The included automated research project on cellular automata is provided solely as a demonstration. Its entire process and results are AI-generated. The resulting papers and experimental findings have no academic value.
