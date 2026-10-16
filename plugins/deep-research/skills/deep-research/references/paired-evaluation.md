# 预先冻结的配对运行

在需要重复比较两个 skill 版本、保留两位独立评委与未完成结果时使用。单对临时盲评仍可用 [compare/score](reporting-and-evaluation.md#固定语料的盲法成对评估)；本协议用独立目录保存全部预定槽位，不调用模型或生成评审答案。

## 冻结材料

准备两版 skill 目录和配置 JSON。三个公开开发案例各重复两次，产生六对、十二次研究；增减案例或重复次数必须在开始前声明。`a/b` 的版本身份只由操作者保存，不在报告标题或内容中声明。

```json
{
  "schema_version": 1,
  "id": "development-pairs",
  "variants": {"a": "/path/to/baseline", "b": "/path/to/candidate"},
  "cases": [
    {"case": "/path/to/skill/evals/pricing-hard-zh/case.json", "rubric": "/path/to/skill/evals/pricing-hard-zh/paired-rubric.json"},
    {"case": "/path/to/skill/evals/trial-hard-en/case.json", "rubric": "/path/to/skill/evals/trial-hard-en/paired-rubric.json"},
    {"case": "/path/to/skill/evals/multihop-long-en/case.json", "rubric": "/path/to/skill/evals/multihop-long-en/paired-rubric.json"}
  ],
  "repeats": 2,
  "seed": 42,
  "run_config": {
    "model_id": null,
    "reasoning": null,
    "settings": null,
    "tool_versions": {"orchestrator": null},
    "max_report_characters": 12000,
    "stop_conditions": ["Answer the question with supported facts and explicit gaps, or stop when the common corpus budget is exhausted."]
  },
  "reviewers": [
    {"id": "J1", "config": null, "calibration": null},
    {"id": "J2", "config": null, "calibration": null}
  ]
}
```

相对路径以配置文件所在目录解析。已知模型 ID、推理设置、生成参数、外部工具版本按实际值填写；未知保留 null，不从应用显示名猜精确身份。`max_report_characters` 是 Unicode 代码点上限，包含引用和空白，不是 token 上限。配置同时冻结停止条件；结束时另写实际停止原因。

评委的 `config` 为实际配置对象或 null；`calibration` 为此前 `calibration-score` 的结果路径或 null。提供校准记录时，必须 `passed=true` 且配置按 JSON 类型完全一致。两个 null 不证明身份或设置已知；`passed_config_unknown` 保留这个限制。没有校准或未核实配置的偏好可以存档，不能据此宣布版本质量改善。校准范围与操作见 [先校准事实评委](reporting-and-evaluation.md#先校准事实评委)。

```bash
research.py paired-prepare config.json --output /tmp/paired-study
```

`manifest.json` 冻结任务、语料、原子 rubric、共同配置、两版资源清单/hash、解释器/依赖/记录器版本、评委槽位及全部 pair ID。运行资源只导出 `SKILL.md`、`requirements.txt`、`scripts/references/agents/assets`，不含 `tests/evals` 或隐藏运行文件。研究者 pack 仅含任务、预算和语料。原目录后续变动不影响快照；改变冻结快照、配置或记录器会被拒绝，应另建 study，不重写旧结果。

三个 `paired-rubric.json` 将费用组成、算术、指标身份、门槛、来源溯源与证据缺口分别评审，不更改旧 evaluator 或既有校准。每项有 `id/requirement/reference/source_ids/critical`。`critical=true` 表示错误会实质改变金额、对象、人口解释、验收或效力/安全/保障结论；缺失和无依据断言仍分别记录。

## 独立运行

按 prepare 返回的 pair 及其 `order` 执行。每个 pair 的后一次研究须等前一次封存；同案例的第二次重复反转版本顺序。每次都新建模型上下文，仅给该次 skill、研究者 pack 和 run，不能给 manifest、rubric、另一版报告、旧答案或评委结果。相同文本允许由不同独立运行产生，不能复制已完成报告作为新运行。

将完全相同的 `run_config` 对象另存为 `run-config.json`，每次显式声明配置与唯一上下文 ID：

```bash
research.py paired-start /tmp/paired-study PAIR_ID --variant a \
  --context fresh-writer-01 --config run-config.json
research.py paired-tool /tmp/paired-study PAIR_ID a -- init \
  --deliverable "Answer the supplied question" --target "The supplied case"
research.py paired-tool /tmp/paired-study PAIR_ID a -- search "task terms" --limit 6
research.py paired-tool /tmp/paired-study PAIR_ID a -- read S1
# 继续 Need、Read、evidence/review、综合等离线操作，在返回的 run_dir 中写 report.md。
research.py paired-finish /tmp/paired-study PAIR_ID --variant a \
  --status completed --reason "All requested items answered or explicitly limited" \
  --report /tmp/paired-study/runs/PAIR_ID/a/report.md
```

`paired-tool` 自动选已冻结脚本，注入固定语料、独立 run 和 local_only，拒绝自选 `--corpus` 及在线发现命令；失败也留命令记录，实际固定语料预算由旧有工具扣除。运行身份、上下文声明、报告和 trace 的 hash 在结束时绑定。completed 需要非空限长报告、匹配任务/语料/预算的 trace 和至少一次成功 Read，pending/超预算不能完成。用 `failed` 或 `budget_exhausted` 保留失败及部分报告，未知的 pending 耗时保持 null。已封存的原稿、快照和工具记录都不可改；同一完成或判断重提幂等，不同内容拒绝。

只把实际平台测量放入 `--measurements measurements.json`：

```json
{
  "scope": "whole_research_run",
  "source": null,
  "end_to_end_seconds": null,
  "model_tokens": null,
  "cost_usd": null
}
```

任何已知数值都要注明实际来源；token 为非负整数，时间/费用为有限非负数。命令累计秒数只在 `cli_usage`，固定语料工具秒数只在 `corpus_usage`；`protocol_span_seconds` 包含排队和操作者间隔，不写入端到端字段。脚本不会测量模型 token 或价格，也不会用 start/finish 的间隔代替它们。

记录器不提供操作系统隔离，不能验证模型/上下文身份、未记录工具或绕过 CLI 的文件读取。调用者负责实际限制可读目录与新上下文。不要把参数一致性或 hash 校验写成已验证的模型公平性。

## 两位评委与固定分母

```bash
research.py paired-blind /tmp/paired-study PAIR_ID
# 分别把两份 blind.json + judgments.template.json 交给两个新的独立上下文。
research.py paired-review /tmp/paired-study PAIR_ID --reviewer J1 --judgments j1.json
research.py paired-review /tmp/paired-study PAIR_ID --reviewer J2 --judgments j2.json
research.py paired-summary /tmp/paired-study
```

两份盲包交换 A/B 并按 A、B 实际展示；隐藏版本身份、run、测量和另一位评委的结论。评委先按每个原子项判断，再比较决策有用性。原子项使用校准的 `supported/refuted/insufficient_evidence/missing/not_assessable` 与 `source_stated/derived/not_assessed`，需要理由、适当逐字报告摘录和有效来源 ID。准确披露未知可 supported；没有信息不能直接判 refuted。每份判断包含自己的唯一 `context_id`，不能复用研究者或其他评委上下文。

每个评委槽位只接收一份不同的独立判断，原判断按内容 hash 保存，互不覆盖；程序验证覆盖与摘录，不替评委判断语义。`critical_errors` 统计关键项的 refuted/insufficient_evidence，`critical_omissions` 单列 missing，not_assessable 仍进入预定事实分母。事实判断及 support_origin 分歧逐项保留，偏好分歧另列，不自动裁决。

`summary.json` 保留全部预定 pair、run 和评委分母。逐案例列已完成对数、完整评审比例、两位评委的 a/b/tie 偏好、偏好共识/分歧、关键错误/缺失、每份报告的字符/UTF-8 字节数和成对开销。只有双方运行完成且该评委的全部原子项可判断，其偏好才进入正式计数；两位评委都满足时才形成偏好共识。缺评显示 pending，无法判断显示 incomplete，均不折算平局。偏好一致也可能有事实分歧，仍需查看逐项记录。

每项费用同时列预定运行数、已测运行数、已测部分之和。只有预定运行全部已测才提供 total；全部未知时 observed_sum 也为 null，不变成零。六对公开开发运行只能反映这批案例，不能支持普遍质量、速度或成本提升。需要未公开且未用于调试的案例，才能进一步检验对已知题目的适应。
