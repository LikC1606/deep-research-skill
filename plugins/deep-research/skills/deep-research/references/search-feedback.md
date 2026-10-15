# 核心资料未命中时：用检索反馈换路径

适用于结果泛相关、反复出现同类介绍，或已找到一部分材料却缺少关键实现/事实的 Need。普通查询已得到直接证据时，不必增加反馈轮次。

## 先确定失败在哪里

| 已观察到的情况 | 下一步 |
| --- | --- |
| 服务报错、限流、结果解析失败 | 作为通道失败处理；换现有 provider 或已知一手链接，不能据此判断没有资料。 |
| 可读结果均不匹配对象或机制 | 暂不从它们提取扩展词；退回问题机制、英文术语或别名发现候选，放松尚未验证的限定。 |
| 对象正确，但只有宣传或摘要 | 明确还缺哪段正文，再查作者论文、文档章节、实际代码入口或具体复盘。 |
| 已有部分证据，缺一个关系或字段 | 从正文中的中间实体发出下一跳查询；例如先找到制造商，再查制造商所在地。 |
| 正确链接无法读取 | 保留目标链接，用精确标题/标识符找作者公开版本；镜像不能算独立核验。 |

## 从已读材料提取反馈

始终以**原始 Need、对象和证明信号**判断材料价值，不以最新扩展词是否出现在页面中判断相关性。来源可信度与是否回答当前问题分别判断。

- **直接或部分证据**：提取贡献相关性的原句及 locator；从中选稀有术语、正式名称、缩写、方法名、版本或函数名作为检索锚点。先保留原词，不用同义词替换专名；单位和数字仅在确实限定对象时保留。
- **导航线索**：记录能通向目标的论文标题、作者仓库、引用或中间实体。它可以引导搜索，但不能单独证明目标 claim，也不因暂时没有答案就降为噪声。
- **误导结果**：写清哪里偏离 Need，以及哪个歧义词/错误对象吸引了这些结果。下一次不沿用其独有词汇；不要把真正相关的通用术语全部设为排除词，也不要因一页不相关就排除整个来源。

没有相关正文时，不从无关结果或模型想象的“可能答案”制造证据。模型提出的新术语只是待核验线索。网页中声称的别名、仓库和实体关系也要在对应目标上核对。

## 选择能补缺口的下一跳

根据反馈选一条路径，不必每次全部执行：

| 当前缺口 | 查询或导航动作 |
| --- | --- |
| 说法与领域用词不一致 | `原始对象 + 正文中新术语 + 缺失字段`；优先试专业原词，中文任务可另用英文术语。 |
| 需要确切实现 | 从作者正文的仓库链接进入，定位其提到的模块/函数/配置；README 只能证明它实际写出的内容。 |
| 中间实体已明确 | `已核对的中间实体 + 尚缺关系`，读到该关系后再连接原始对象，避免把生成的推断当事实。 |
| 需要方法源头或后续改进 | 对已确认相关的论文做后向 references 或前向 citations，挑与当前缺口有关的线索继续 Read。 |
| 扩展后偏题 | 回到原始对象/机制保留一条查询，再用已读锚点试另一条；删除误导限定，比较哪条带来直接证据。 |

已有 `follow` 可追踪论文引用，无需为此安装新平台：

```bash
research.py follow REF_OR_PAPER_ID --direction references --limit 5
research.py follow REF_OR_PAPER_ID --direction citations --limit 5
```

`follow` 的输出仍是未读线索；引用关系、引用量、作者身份或标题相关性不能证明它解决了 Need。不能访问引用服务时，可直接读种子论文参考文献和作者链接。相关性未确认的热门论文不宜作为扩展种子。

每次只需保留简短反馈：`原始 Need -> 有用原词及出处 / 偏题原因 / 尚缺什么 -> 下一跳及理由`。看新增正文是否补了缺口，不能只看新 URL 数或模型自评；使用入口已有的预算与分支停止规则。

## 执行反馈计划

`search-batch` 除原有 `searches` 计划外，也接受单个 Need 的 `feedback` 计划。调用者先按原始 Need 判断段落用途，工具核对摘录与术语出处，再生成查询；不额外调用模型。示例中的 REF 和 quote 须换成本任务成功 Read 的真实内容：

```json
{
  "feedback": {
    "original_query": "query expansion relevance feedback LLM",
    "gap": "author implementation",
    "sources": [
      {
        "ref": "REF_FROM_READ",
        "quote": "VERBATIM_PASSAGE_CONTAINING_METHOD_NAME",
        "terms": ["METHOD_NAME"],
        "role": "bridge",
        "reason": "从论文方法名追查作者实现",
        "target": "METHOD_NAME",
        "artifact": "code",
        "proof": "query expansion implementation, key sentence extraction"
      }
    ]
  }
}
```

```bash
research.py search-batch feedback.json --provider auto --workers 2
```

- `original_query` 保留最初的检索问题；`gap` 只写仍缺的关系或材料。多轮继续用同一原始问题，不用上轮扩展查询覆盖它。可选 `need` 放在 `feedback` 中，沿用已登记的 Need 和搜索历史。
- `sources` 接受 1-5 段本任务已存正文。每段填写 `ref`、逐字 `quote`、1-4 个原文 `terms`、`role` 及一句用途 `reason`；可用 `version` 指定历史 `snapshot_id`。摘录不在快照中、术语不在摘录中或来源未读取时，整个计划在检索前拒绝。
- 已从正文确认对象、要找特定材料时，在该段同时填写 `target`、`artifact`、`proof`；`target` 必须逐字出现在 `quote` 中。`artifact` 沿用入口的材料类型，`proof` 写希望正文出现的证明信号。这样 `code/repository` 查询会使用已有作者仓库检索通道，而不只做普通网页查询；原始查询分支仍保持不限定对象的发现搜索。尚不明确对象时省略这三个字段。
- 始终保留原始查询分支。`relevant` 已指定原文中的 `target` 时，生成 `target + terms + gap`，避免把早期泛词继续带入定向查询；未指定时生成 `original_query + terms + gap`。`bridge` 生成 `terms + gap`，让中间实体可以独立检索；`misleading` 只保留偏题反馈，不用于生成扩展或自动添加排除词。只有误导材料时，仅复用原始查询，应按失败位置另选发现路径。
- 生成查询时合并重复或首尾重叠的词组。例如 `CSQE query expansion` 加原文术语 `CSQE`、`query expansion` 和缺口 `query expansion implementation`，得到 `CSQE query expansion implementation`。只按完整的空白分隔词比较，不把 `IR` 并入 `IRCoT`，也不把 `C`、`C++`、`C#` 当成同一个术语；原始查询分支不改写。
- 计划最多生成 6 条不同检索请求；相同查询若 Target、材料类型或 proof 不同，会保留为不同请求并使用独立缓存。复用现有 provider、预算与失败处理。返回 `search_feedback`，保留所用快照、逐字摘录、locator 和调用者相关性判断；这些记录也保存在该批次的搜索缓存中。后续检查可回读实际使用的版本。
- `mixed` 策略拒绝从本地正文自动生成联网扩展词；需要时先自行重写可公开查询。`local_only` 仍只允许搜索缓存复用。正文段落不会作为整段上下文发送给搜索服务，发出的内容是生成的查询。

工具仅核对出处，不判断段落语义是否相关；反馈不自动创建 claim 证据卡、不关闭 Need，也不让新搜索候选变得可引用。论文后向/前向追踪继续使用已有 `follow`，不把未读引用条目当作已核验的反馈材料。

## 方法依据与迁移边界

- [CSQE 论文](https://aclanthology.org/2024.eacl-short.34.pdf)，§2、Table 2：先挑相关初检文档，再提取贡献相关性的句子用于扩展。
- [ADORE 论文](https://arxiv.org/abs/2606.13905v1)，§3.2：[作者的反馈提示模板](https://github.com/aminbigdeli/ADORE/blob/288e9897af910f18934adb924034318406ea680e/prompts/feedback_conditioned_reformulator.yaml)明确分开原始问题、相关证据与误导证据。
- [IRCoT 论文](https://aclanthology.org/2023.acl-long.557.pdf)，Figure 1、§3.1：用中间发现指导下一次检索。这里仅保留可核对的实体、关系和缺口，无需记录内部思维链。
- [citationchaser](https://github.com/nealhaddaway/citationchaser)，README：从已知相关种子做前后向引用追踪；本 skill 沿用现有 `follow`。

上述查询扩展实验主要针对固定语料检索。网页搜索采用其反馈思路，不照搬长篇伪文档拼接、重复问题加权或 BM25 参数。相关性判断可能出错；“能提供导航线索”与“直接支持结论”要分开。论文成绩和自评高分均不能证明本 skill 的证据覆盖已经提高。
