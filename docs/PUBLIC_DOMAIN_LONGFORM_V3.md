# 公版融合长篇 v3：古代原典证据闸门

## 适用范围

只修改 `public_domain_rewrite`。普通文章、古文/佛经、漫画、小说、家长里短长篇和 Epic Novel Engine 均不改变。

正式公版模式仍要求 8–10 本逐本确认权利状态的公版来源。`research_preview` 接受 5–7 份现代整理材料，但这些材料永远只是发现线索和来源重合排查输入，不是写作证据。

## 双账本

1. `01_discovery_only_ledger.json`：保存从现代整理材料发现的候选命题。所有记录均为 `discovery_only`、`generation_eligible=false`。
2. `01_verified_primary_ledger.json`：候选必须通过 OpenAI 联网搜索命中古代原典托管站点，且模型返回的证据 URL 必须真实存在于 web-search 工具来源列表，才可晋级为 `verified_primary`、`generation_eligible=true`。

当前允许的古代原典托管域名：

- `zh.wikisource.org`
- `cbetaonline.dila.edu.tw`
- `tripitaka.cbeta.org`

计划、写作、修复、逐章审稿和全稿终审只读取第二本账本。现代书名、现代作者、现代正文、现代评论和 discovery statement 都不能进入下游提示。

## 晋级条件

每条晋级证据必须具备：

- 古代原典书名；
- 卷、品、章、经号或其他可复查定位；
- 原典短证据（只保存在内部账本，不传给写作模型）；
- 本次 web-search 工具实际返回、且属于允许域名的 URL；
- 克制的中性 statement 与限定词；
- 原始 discovery claim ID 和发现材料 ID，仅供审计。

研究试稿至少需要 18 条核验证据、3 部不同古代原典，并覆盖至少 3 份发现材料。任一条件不足即进入 `manual_review`，不生成或交付终稿。

## 硬拦截

以下任一情况阻止章节和全稿通过：

- 现代书名、现代作者或出版社进入正文；
- 出现“根据材料”“片段中”“研究材料”“现代整理本”等创作过程措辞；
- 正文引用的 `《书名》` 不在该任务的古代原典核验账本；
- allowed evidence 混入 `discovery_only` 或 `generation_eligible=false` 记录；
- web-search 工具没有实际返回模型声称使用的 URL；
- URL 不属于允许的古代原典托管域名；
- 事实、人物、故事、数字、引语、因果、心理或评价没有 verified-primary 支持；
- 旧 v2 报告、旧缓存或不同策略版本试图复用；
- 来源连续重合、篇幅、章节、语言或其他原有质量闸门失败。

## 任务状态

只有原典核验、逐章事实审查、来源重合和全稿终审全部通过，任务才可写入 `Chapter`、标记 `completed` 并允许下载。原典证据不足属于内容质量未达标，状态为 `manual_review`；API 或联网故障不产生降级稿。

关键任务字段：

- `source_provenance_policy=discovery_only_then_verified_primary`
- `primary_verification_required=true`
- `primary_verification_status=pending|passed|failed`
- `verified_primary_claim_count`
- `public_domain_policy_version=public-domain-longform-v3-primary-evidence`
