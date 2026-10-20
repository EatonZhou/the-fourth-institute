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

**Currently supported: Linux with Python 3.12 and venv support.**

Application code is shared in `code/`. The example seed lives in `instance/example_instance/seed/`; private instance data stays under its own `instance/<name>/`. From the repository root, run:

```bash
python3 prepare_install.py
python3 code/scripts/tfi_release_install.py --help
```

The first command verifies the example seed and builds an installation bundle under `.build/example_instance/engineering/`. It does not change running instance data. The second displays the installer's commands and options.

To install an instance, follow [the installation guide](INSTALL.md) to create `target.json` and complete installation, initialization verification, and startup. The two commands above are the starting point, not a complete installation. The installer can use configured dependencies or prepare the bundle's supported dependencies for the instance; downloading missing packages requires internet access.

After installation:

1. Follow the guide's `first-admin` step to create the first administrator and sign in.
2. Configure your AI provider accounts and model backend.
3. Choose an included research team, add your materials, and start with a small task whose results you can check.

You can ask your own AI agent running on your machine to read [the installation guide](INSTALL.md), prepare the configuration, complete the installation, and help investigate any problems.

### Configure AI Accounts

You can also ask your local AI agent to help register your AI provider or API with TFI Portal, prepare the required runtime, and configure the model backend. Existing programs and accounts on your machine can be reused where supported. In the Portal interface, complete account setup, test a model response, and select a default AI before starting Resident conversations.

See [the installation guide](INSTALL.md) for service status, logs, stopping an instance, and upgrades.

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
