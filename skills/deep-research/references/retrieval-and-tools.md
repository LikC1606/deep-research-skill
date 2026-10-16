# 检索工具与专用通道

首次使用脚本、调整 provider、定向查代码或研究竞赛经验时读取。通用研究流程在 SKILL.md；状态、缓存、批量查询和预算在 [workspace-and-evidence.md](workspace-and-evidence.md)。

## 运行与依赖

```bash
export DEEP_RESEARCH_RUN_DIR=/path/to/this-task
uv run --with-requirements <skill-dir>/requirements.txt \
  <skill-dir>/scripts/research.py search "MECHANISM + CURRENT NEED"
uv run --with-requirements <skill-dir>/requirements.txt \
  <skill-dir>/scripts/research.py search "TARGET + CURRENT NEED" \
  --target "EXACT TARGET" --artifact TYPE --proof "EXPECTED BODY SIGNALS"
uv run --with-requirements <skill-dir>/requirements.txt \
  <skill-dir>/scripts/research.py read REF_OR_URL_OR_LOCAL_PATH --term TERM --term TERM
```

uv 会建立或复用隔离的依赖环境，首次运行可能联网安装 requirements；不要安装进不相关项目环境。未知方法名时使用第一种发现查询，读到候选后再定向核验。已知 URL 或用户文件可直接 Read。

`TYPE` 为 `official|paper|repository|code|issue|benchmark|postmortem|writeup`；`proof` 只写正文应出现的 2–4 个信号，不写猜测的答案。`rank/solution` 用于竞赛，`repository/implementation` 用于代码，`postmortem/writeup` 用于复盘；不把所有限定词塞进每次查询。专业短语可加引号，例如 `"citation evaluation" entailment`，避免用长串通用指标掩盖问题主语。

成功 Read 保存正文提取快照、`snapshot_id/content_hash/content_scope/body_path/locators`。再次查同一来源用本地 `find/open`；长文命中按 `next_offset` 翻页，参数及计数见 [阅读与回查](workspace-and-evidence.md#阅读与回查)。证据卡引用历史快照时带 `--version SNAPSHOT_ID`；需要更新才 `read --refresh`。相同搜索默认复用当前任务候选；只恢复失败请求用 `search --retry-failed`，整次更新候选用 `search --refresh`。`search --need ID` 将搜索及失败关联已登记 Need。`seen_before/new_ref_count` 提示重复线索，不能代替新证据判断。

## Provider 与错误语义

默认 `--provider auto` 的普通发现先尝试 AnySearch；定向 `repository/code/writeup/postmortem` 先查 GitHub，Kaggle `official` 先查官方元数据。专用候选不足或查询/Target 覆盖有缺口时再泛搜；AnySearch 不足时补 DuckDuckGo/Bing/360、arXiv/OpenAlex 等直接渠道。`recall_checks` 是标题/snippet 级启发式，不证明正文充分或多个来源独立。

| 模式 | 使用条件与行为 |
| --- | --- |
| `auto` | 专用通道充分时可结束发现；否则 AnySearch，再按缺口补直接渠道 |
| `hybrid` | 专用通道之后仍尝试聚合和直接渠道，查询发送给更多服务 |
| `direct` | 先查适用的专用 API，有缺口再泛搜；全部为直连，仍会联网 |
| `anysearch` | 所有 artifact 均仅调用 AnySearch，包括 Kaggle official；失败报错，不额外直连 |

`providers_planned` 列出有条件的渠道顺序和已知查询参数，`direct` 是泛搜渠道组；计划不表示每一步已经发送请求。实际尝试看 `providers_used/provider_results/provider_errors`。各渠道按有效请求参数，在同一次逻辑 Search 内复用相同请求；GitHub 区分 query/limit，Kaggle 区分 URL/响应大小上限。成功、空结果和失败均复用，记录 `request` 和 `reused`，不重复发送或重复计错误。单次执行复用不跨并发 worker；显式恢复磁盘缓存时，按原有效请求分别重试，而非按 provider 名称整组刷新。

一次未缓存搜索只扣一次逻辑 Search，各渠道共享 HTTP 额度；后续预算耗尽时保留先前候选并返回 `partial`。没有可用候选且发生渠道错误时不缓存失败为空；成功空响应可缓存为 `empty`。`provider_results` 记录各渠道的 `ok/empty/error`；Kaggle 元数据、列表与分页操作分别记录。搜索事件保存最终合并候选和状态，包含专用通道充分而未泛搜的情况。部分成功继续 Read 有价值的命中；需要恢复渠道时显式 `--retry-failed`。恢复只访问原失败请求，新可达依赖以 `skipped` 留待 refresh；恢复改变路由时仍组合此前执行的渠道，保留已恢复候选。旧格式缓存和配置变化需整次 refresh。具体缓存、时间语义和批次状态见 [workspace-and-evidence.md](workspace-and-evidence.md#搜索复用与下一步选择)。

AnySearch 仅从 `ANYSEARCH_API_KEY` 读取可选 Key，不注册账户、不写 `.env`，不把 Key 放进 query。`DEEP_RESEARCH_ANYSEARCH_TIMEOUT` 可调整请求超时（2–30 秒）。所有聚合结果都是未读线索，引用仍以原始材料成功 Read 为准。

## 候选保留与诊断

公共排序阶段用标题/snippet 的词覆盖排序，不用固定词数门槛清空弱匹配候选；仍受 URL 策略、通道定向校验和结果数上限约束。同 URL 保留相关评分较高的原始卡片，不拼成新的来源摘要。同标题仅产生 `similar_titles_require_read` 提示，不能据此判定镜像、来源独立或版本相同。arXiv 的 abs/pdf/html/DOI 只在 ID 与显式版本一致时归并；无版本、v1、v2 分别保留。

`discovery_diagnostics` 描述命名的排序阶段：`direct_ranking` 是直连候选排序，`combined_ranking` 是聚合与直连合并排序。每阶段从通道已解析的卡片开始，分别记录输入数、重复 URL、URL 策略拒绝、明确文档别名、结果上限省略、返回数及保留的弱匹配数；不是搜索服务的原始命中总数。重复 URL 先计数，其余淘汰数依次相加可还原输入数。定向通道可能在这些阶段之后加入候选，因此阶段返回数不一定等于最终页长度；`recall_checks.remaining_gaps` 针对最终返回页重算。

诊断随搜索缓存保存。旧缓存保留原有结果和字段；需要新排序与计数时显式 `search --refresh`，`local_only` 仍只能重放已有缓存。计数和词覆盖只解释候选选择，不能用于关闭 Need 或证明正文支持。

## 竞赛与高分方案

Kaggle 或其他竞赛先读官方 Evaluation/Data 确定任务边界，再用榜单或公开身份定位参赛者。精确官方对象未确认时，不把同名比赛、另一年份或通用方案混进当前任务。脚本 scope gate 要求先读官方材料，后续第一条方案查询消费从官方正文提取的 Pivot；返回 `metadata` 时保留缺少 Evaluation/Data 正文的缺口，不把门控通过当作规则已齐全。

官方 Read 已确认当前 Kaggle 对象时，讨论区检索直接使用保存的比赛 slug，避免再次猜名称或遍历列表。复用要求完整 Target/canonical 身份匹配，显式比赛 slug 和年份不得冲突；名称相近的另一比赛重新发现。scope/Pivot 拒绝不扣 Search，逻辑预算不足也不消费 Pivot。

研究重点是一手复盘、讨论、访谈与仓库记录，提取“验证设计 → 迭代顺序 → 决策依据 → 失败尝试 → shake-up/泄漏风险 → 算力时间取舍 → 可迁移经验”。成绩证明结果和身份，不证明方法价值；选手名气只能帮助发现资料。至少比较两份独立复盘的共识与分歧；如只能找到一份，明确说明限制，不用列表或转载凑数。

竞赛发现和有共享 scope 的批次按顺序执行；论文引用追踪与章节综合见 [reporting-and-evaluation.md](reporting-and-evaluation.md)。
