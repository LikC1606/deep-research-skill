# 用于提高核心资料命中率的研究资料

调研日期：2026-10-16。本轮围绕“怎样找到真正有用的原始材料”查资料，读取三篇论文全文、两份作者源码/提示模板和 citationchaser 的 README，并克隆两个作者仓库。未运行外部项目或付费模型，未做本 skill 的新旧版本效果比较。

## 必答问题

| Need | 要查清的内容 | 所需证据 |
| --- | --- | --- |
| N1 | 如何减少用户表述与资料术语不匹配？ | 从检索正文提取扩展词的算法与作者实现。 |
| N2 | 如何避免查询越扩展越偏题？ | 原始问题锚定、相关/误导反馈及停止规则。 |
| N3 | 有一份好材料后怎样继续找核心资料？ | 中间实体驱动检索、种子论文的前后向引用追踪。 |

## 最有用的四项资料

| 资料 | 本轮核对到的机制 | 适合借鉴的地方 | 边界 |
| --- | --- | --- | --- |
| [Corpus-Steered Query Expansion with Large Language Models / CSQE](https://aclanthology.org/2024.eacl-short.34.pdf) | §2、Table 2：初次检索后选相关文档，提取贡献相关性的关键句，与原问题及模型生成扩展组合检索。 | 新查询优先采用相关正文里的专业原词，而不是只做同义改写。 | 固定语料/BM25 设置；不能把模型生成的扩展当作已查证事实。 |
| [ADORE](https://arxiv.org/abs/2606.13905v1) | §3.2：生成扩展、观察检索结果、按原始问题判断相关性，再反馈应强化、补充或抑制的内容。 | 同时记录有效锚点和误导词；下一轮以实际结果为依据，保留原始 Need。 | 模型相关性判断不是人工真值；论文收益未在本 skill 复现。 |
| [IRCoT](https://aclanthology.org/2023.acl-long.557.pdf) | Figure 1、§3.1：检索与中间推导交替，后续查询使用新发现；示例先查到制造商，再查其所在国家。 | 直接查询缺少关系时，先寻找可核对的中间实体，再围绕该实体补下一跳证据。 | 原方法使用生成的推理句；本 skill 只需提取可核对实体和关系，仍需验证每一跳。 |
| [citationchaser](https://github.com/nealhaddaway/citationchaser) | README：以已知相关研究为种子，向后找参考文献、向前找引用它的研究。 | 找源头、后续改进及作者相关工作，补关键词搜索漏掉的材料。 | 本轮只读 README，未检查 R 实现；其 Lens API 需要 token，本 skill 可用已有 Semantic Scholar `follow`。 |

这四项方法可以连接成一条策略：**用相关正文提取真实术语，按原问题检查扩展是否有效，再沿实体和引用补缺口。** 这是对已读方法的迁移建议，不是本地效果结论。

## 作者实现：可以直接对照的代码

### CSQE

[已读作者代码](https://github.com/Yibin-Lei/CSQE/blob/f03960cf6efb90a8684ed88ec23282995e2d9f4b/csqe.py)：

- `progressive_query_rewrite`，约 323-420 行：先在提示中要求选相关初检文档并提取关键句；`csqe` 分支分别生成知识扩展与语料扩展，再与原查询合并。
- `extract_key_sentences`，31-35 行：通过引号匹配提取模型输出文本，**没有在这里检查这些句子是否逐字存在于初检材料**。因此借鉴“从正文抽原词”，不能把模型给出的引号当作真实性保证；现有 Read 摘录和 locator 更适合作为依据。

### ADORE

[已读反馈模板](https://github.com/aminbigdeli/ADORE/blob/288e9897af910f18934adb924034318406ea680e/prompts/feedback_conditioned_reformulator.yaml)，1-21 行：明确保留 `ORIGINAL QUERY`，将相关证据和应避开的误导证据分开；相关证据中命名实体、缩写和技术短语保留原词。`config.yaml` 第 36 行选择了该模板。

本地还检查了 `src/agent.py`：`_filter_new` 按文档 ID 去重；`_termination_after_round` 在本轮评分全为 3 或连续缺少新文档时可以停止。**这些条件适合控制检索轮次，不能据此关闭研究的全部 Need。** 高相关结果仍可能集中在同一方面。

另一个不宜照搬的细节是其相关性分档：`prompts/judge.yaml` 把“相关但未回答”的段落设为 1，而反馈模板将 1 分段落列入 avoid。跨网页多跳研究中，论文标题、制造商名称或作者仓库可能只是导航线索，却对找到核心材料有价值。因此本 skill 应区分直接证据、导航线索和真正误导结果。

## 本轮采用的最小改进

在 `references/search-feedback.md` 增加按需读取的搜索补救方法，并由 `SKILL.md` 的核心资料未命中段落路由：

1. 区分通道失败、对象不匹配、材料不够深、关系缺失和正文不可读。
2. 从已读相关段落提取原词及出处，明确误导词为何偏题。
3. 下一跳可选原词查询、作者源码导航、中间实体检索或前后向引文追踪。
4. 原始 Need 始终保留；导航材料不冒充答案，也不被当作无关噪声删除。

没有新增模型依赖、评分系统或搜索后端。现有 `search-batch` 可用于少量独立查询，依赖新证据的下一跳仍顺序执行。

## 实际检索观察与未完成核验

本轮 AnySearch 返回 `no parseable search cards`，属于通道失败；随后改用直连查询 `query expansion relevance feedback LLM`，读到 ADORE 并从论文找到作者模板。Crossref 元数据定位到 CSQE 和 IRCoT 后，直接读取 ACL 全文。GitHub API 随后出现 403，但作者仓库可直接浅克隆。这说明一种通道失败不能据此判断没有资料；本次成功路径也不是提升效果的对照实验。

另发现 *Explaining When PRF Fails: Participatory Auditing for Selective Query Expansion*，尚未读取正文，仅作为后续线索，不采用搜索摘要中的结论和数值。

本轮已覆盖 N1-N3 的方法与代码证据，停止新增同类链接。真正的效果仍需在用户实际找不到的资料任务上观察：新增正文是否补上缺口，而不是链接数、轮数或自评是否增加。

## 本地参考版本

路径相对于工作区的 `deep-research-references/`，浅克隆并稀疏检出相关源码，未执行外部代码：

| 仓库 | Commit | 本地目录 |
| --- | --- | --- |
| CSQE | `f03960cf6efb90a8684ed88ec23282995e2d9f4b` | `Yibin-Lei/CSQE/` |
| ADORE | `288e9897af910f18934adb924034318406ea680e` | `aminbigdeli/ADORE/` |

克隆记录追加至本地 `manifest.json`。来源原始快照保留在本次独立运行目录 `/tmp/deep-research-source-discovery-20261016/`，它不是仓库内的永久归档。
