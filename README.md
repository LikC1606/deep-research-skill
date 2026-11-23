<!-- README_SYNC: source=468295cae9529892902841852d0e79d2c16a190b; updated=2026-11-23 -->
<p align="center">简体中文 · <a href="./README_EN.md" lang="en">English</a></p>

# Deep Research

给 Codex 及其他支持 Agent Skills 的客户端提供一套可回查的深度研究流程：先明确哪些证据会改变结论，再直读原始正文，依据读到的术语、冲突与线索决定下一步检索方向。

[![Stars](https://img.shields.io/github/stars/LikC1606/deep-research-skill)](https://github.com/LikC1606/deep-research-skill/stargazers)
[![Codex Plugin](https://img.shields.io/badge/Codex%20Plugin-v1.0.0-10A37F)](./plugins/deep-research/)
[![skills.sh](https://skills.sh/b/LikC1606/deep-research-skill)](https://skills.sh/LikC1606/deep-research-skill)
[![MIT License](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

觉得这套查证方法有用，欢迎 [Star 收藏](https://github.com/LikC1606/deep-research-skill/stargazers)。

## 安装与试用

有 Node.js 和 Skills CLI 兼容客户端时，可直接安装独立 Skill：

```bash
npx skills add LikC1606/deep-research-skill --skill deep-research
```

如果你的 Codex 支持插件市场，可改用以下方式：

```bash
codex plugin marketplace add LikC1606/deep-research-skill
codex plugin add deep-research@likc1606-skills
```

也可以请 Codex 通过 Skill Installer 安装：

```text
$skill-installer install deep-research from LikC1606/deep-research-skill
```

安装后可以问：

> 用 $deep-research 对比这两个方案。先列出会改变选择的证据，再读最直接的来源，给出结论、引用位置和仍未核验的事项。

## 一份能审阅的研究结果

| 阶段 | 如何工作 |
| --- | --- |
| 确定要查的内容 | 从交付物列出必须回答的问题，区分会改变决策的信息和背景知识。 |
| Search → Read → Pivot | 搜索结果当线索；正文与准确定位才可作为引用依据。来源冲突时检查版本、时间和定义。 |
| 保存证据 | 保存快照、定位、原文摘录与历史版本；记录每个问题的缺口、失败尝试和停止原因，便于断点续研。 |
| 综合 | 围绕决定说明结论、证据、推断和未知项，交代结果适用条件。 |

单点核验用 **Light Search**；有多项材料、冲突或迁移风险时用 **Deep Search**。例如复盘竞赛时，可先用官方规则和榜单确定边界，再读选手的复盘、仓库和失败尝试，比较能迁移的验证设计和资源取舍。名次只能支持其对应结果，不能证明方法适合新任务。

默认引用回查可验证是否读过、位置是否存在；论断是否得到原文支持，仍要核对。可为任务选择本地、允许联网或混合来源策略。具体依赖、可用 provider 和联网范围见 [检索工具说明](./skills/deep-research/references/retrieval-and-tools.md)。

## 文件与验证

- [Skill 入口](./skills/deep-research/SKILL.md)说明任务规划与证据标准；[证据工作区](./skills/deep-research/references/workspace-and-evidence.md)记录快照、预算和恢复。
- [报告与评测](./skills/deep-research/references/reporting-and-evaluation.md)覆盖分章综合、文献引用追踪、报告审计和固定材料盲评；[实际评测归档](./research/evaluation-2026-10-15/README.md)给出当次语料与结果边界。
- [市场清单](./.agents/plugins/marketplace.json)引用 Codex 插件包。修改独立版本后同步插件副本并检查一致性：

```bash
python scripts/sync_skill.py
python scripts/sync_skill.py --check
```

本仓库采用 [MIT License](./LICENSE)。遇到可复现的问题，欢迎到 [Issues](https://github.com/LikC1606/deep-research-skill/issues)提供任务描述、来源范围和出错步骤。
