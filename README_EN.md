<!-- README_SYNC: source=468295cae9529892902841852d0e79d2c16a190b; updated=2026-11-23 -->
<p align="center"><a href="./README.md" lang="zh-CN">简体中文</a> · English</p>

# Deep Research

An auditable research workflow for Codex and other Agent Skills clients: define what evidence would change the decision, read the primary source itself, and pivot based on the terms, conflicts, and citations you uncover.

[![Stars](https://img.shields.io/github/stars/LikC1606/deep-research-skill)](https://github.com/LikC1606/deep-research-skill/stargazers)
[![Codex Plugin](https://img.shields.io/badge/Codex%20Plugin-v1.0.0-10A37F)](./plugins/deep-research/)
[![skills.sh](https://skills.sh/b/LikC1606/deep-research-skill)](https://skills.sh/LikC1606/deep-research-skill)
[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

[Star the repository](https://github.com/LikC1606/deep-research-skill/stargazers) to keep this evidence-driven research workflow handy.

## Install and try it

With Node.js and a Skills CLI compatible client, install the standalone Skill:

```bash
npx skills add LikC1606/deep-research-skill --skill deep-research
```

If your Codex client supports plugin marketplaces, you can use:

```bash
codex plugin marketplace add LikC1606/deep-research-skill
codex plugin add deep-research@likc1606-skills
```

You can also ask Codex to install the Skill:

```text
$skill-installer install deep-research from LikC1606/deep-research-skill
```

Then give it a real decision to investigate:

> Use $deep-research to compare these two options. Identify the evidence that would change the choice, read the most direct sources, and give a recommendation with citations and unresolved questions.

## What a research result contains

| Step | What the Skill does |
| --- | --- |
| Define the needs | Work back from the deliverable to the questions that can change it. |
| Search → Read → Pivot | Use search results as leads. Read the source before citing it; check version, date, and definitions when sources disagree. |
| Preserve evidence | Keep snapshots, locators, excerpts, and revisions. Track missing evidence, failed attempts, and stopping reasons so work can resume. |
| Synthesize | Explain the conclusion, evidence, inferences, and remaining uncertainty in terms of the decision. |

Use **Light Search** for narrow checks and **Deep Search** for broader questions, conflicting sources, or evidence that may not transfer. For a competition retrospective, for example, confirm the rules and standings first. Then read participants' writeups, repositories, and failed experiments to compare validation choices and resource tradeoffs. A ranking supports the result it records, not performance in a new task.

Citation checks can detect unread or missing references; the reviewer still judges whether the excerpt supports the claim. Tasks can use local, web-enabled, or mixed source policies. See the [retrieval guide](./skills/deep-research/references/retrieval-and-tools.md) for dependencies, providers, and network boundaries.

## Repository and verification

- The [Skill entry point](./skills/deep-research/SKILL.md) defines the method. The [evidence workspace guide](./skills/deep-research/references/workspace-and-evidence.md) covers saved sources, budgets, and resumable tasks.
- [Reporting and evaluation](./skills/deep-research/references/reporting-and-evaluation.md) covers chapter synthesis, citation traversal, audits, and fixed-corpus blind comparisons. The [evaluation archive](./research/evaluation-2026-10-15/README.md) states the corpus and limits of that run.
- The [marketplace manifest](./.agents/plugins/marketplace.json) points to the Codex plugin. After editing the standalone Skill, sync the plugin copy and check parity:

```bash
python scripts/sync_skill.py
python scripts/sync_skill.py --check
```

Released under the [MIT License](./LICENSE). For a reproducible problem, share the task, source scope, and failure step in [Issues](https://github.com/LikC1606/deep-research-skill/issues).
