# Deep Research

[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-deep--research-111827)](./skills/deep-research)
[![Codex Plugin](https://img.shields.io/badge/Codex%20Plugin-v1.0.0-10A37F)](./plugins/deep-research)
[![skills.sh](https://skills.sh/b/LikC1606/deep-research-skill)](https://skills.sh/LikC1606/deep-research-skill)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

让 Agent 不再只搜表层关键词，而是从任务反推真正需要的证据，阅读正文，并形成可核验、有启发的总结。

Task-driven, evidence-first research for Codex and other Agent Skills compatible tools.

## Why

很多 Agent 收到“帮我查资料”后，会搜索用户原话、罗列一批相关链接，再逐篇复述。真正的问题没有被回答：

- 哪些信息会改变最终方案或判断？
- 什么正文证据才足以支持结论？
- 搜到一个术语、作者或失败原因后，下一步应该追查什么？
- 多个来源之间有哪些共识、冲突、机制和适用边界？

Deep Research 用一个简洁闭环解决这些问题：

```text
Deliverable -> Need Map -> Search -> Read -> Pivot -> Synthesis
```

## What It Does

- **从交付物反推资料需求**：只研究会影响结果的关键问题，不机械扩展关键词。
- **Search -> Read -> Pivot**：搜索结果只是线索；必须阅读正文，并用正文中的新术语继续追查。
- **证据优先**：标题、snippet 和聚合页不能直接支持最终结论。
- **跨来源综合**：提炼共识、冲突、机制、取舍、适用边界和可行动启发，而非逐篇摘要。
- **Light / Deep 两种模式**：小问题快速核验，复杂任务覆盖多个 Need 并检查冲突与风险。
- **可核验输出**：事实、推断和未知项分开，引用只能来自实际读取的正文。

## Competition Research

参加 Kaggle 或其他比赛时，最高分模型往往不是最有价值的信息。真正值得寻找的是优秀选手如何做决策：

```text
官方规则确定边界
-> 榜单定位参赛者
-> 选手复盘 / 讨论 / 访谈 / 仓库
-> 验证设计 / 迭代顺序 / 失败尝试 / 资源取舍
-> 比较不同选手的共识、分歧与可迁移经验
```

名次只用于确认身份和结果，选手名气只用于发现资料。最终价值由正文中的具体经验、证据和迁移边界决定。

## Install

### Agent Skills CLI

```bash
npx skills add LikC1606/deep-research-skill --skill deep-research
```

### Codex Plugin Marketplace

```bash
codex plugin marketplace add LikC1606/deep-research-skill
codex plugin add deep-research@likc1606-skills
```

### Codex Skill Installer

Ask Codex:

```text
$skill-installer install deep-research from LikC1606/deep-research-skill
```

## Try It

```text
Use $deep-research to investigate this task. First identify what evidence would
change the answer, then read the strongest sources and synthesize the result.
```

```text
Use $deep-research to study this Kaggle competition. Focus on participant
postmortems, validation decisions, failed experiments, and transferable lessons.
```

```text
Use $deep-research in Light Search mode to compare these two technical options
and clearly separate evidence, inference, and unresolved gaps.
```

## Research Output

The Skill organizes the final result around the task rather than the browsing history:

```text
Conclusion: direct answer to the deliverable
Evidence: each Need, its finding, source, locator, and scope
Synthesis: agreements, conflicts, mechanisms, tradeoffs, and implications
Limits: missing evidence, uncertainty, and applicability boundaries
```

## Distribution Layout

- [`skills/deep-research/`](./skills/deep-research): standalone Agent Skill used by the Skills CLI.
- [`plugins/deep-research/`](./plugins/deep-research): OpenAI plugin package containing the same Skill.
- [`.agents/plugins/marketplace.json`](./.agents/plugins/marketplace.json): Codex marketplace catalog.

The standalone and plugin copies are kept byte-identical.

## License

[MIT](./LICENSE)
