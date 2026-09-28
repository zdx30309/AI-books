# 通用短篇（general_short）：分层提示词与差异化闸门

## 问题

`通用短篇` 原来把《2万字书稿创作指令词 v09》整份 6 万字塞进主笔上下文，
其中 **63.6%** 是两篇成品样稿、三份检测报告和改稿范例，而真正约束写作的规则
只有 **21.8%**。范文成了最强的风格锚点，主笔模仿其"事实—数字—流程—判断"
文体，产出自然与灵感原文的结构高度对齐。同时 v09 里唯一能压住结构的
`第十节 + 附录六` 被适配说明砍掉，工程侧也没有任何结构级检测，因此
"和视频相似度还是很高"没有任何拦截。

## 现在的三层结构

| 层 | 资产 | 作用 |
| --- | --- | --- |
| 前置 | `app/services/general_short_fingerprint.py` | 动笔前把灵感原文抽象成结构指纹（论点单元 / 案例次序 / 叙事主线 / 关键词 / 领域），作为主笔必须**规避**的对照物 |
| 写作 | `app/prompts/general_short/` | 只装写作规则，共 8 节约 1.3 万字；两篇样稿、三份报告、附录六全部移除 |
| 后置 | `app/services/general_short_differentiation.py` | 终稿按冻结公式打分：`S=A×0.40+B×0.30+C×0.30`、`composite=S×0.60+M×0.40`，≥50% 判高危 |

## 提示词包

```
backend/app/prompts/general_short/
├── manifest.json          # 版本 + 分节 sha256（装载时强校验）
├── sections/              # 8 个写作分节（01 适配 … 08 防重复）
├── stages/                # draft / structure_extract / differentiation_audit
└── archive/               # v09 原始单文件，只作证据保留，不参与装载
```

重新生成：

```bash
cd backend
python scripts/build_general_short_bundle.py     # 从 v09 原始文件重建分节
python scripts/build_general_short_manifest.py   # 重算哈希与 stage 映射
```

`app/prompts/general_short/**` 在 `.gitattributes` 中标记为 `-text`：
分节字节参与 sha256 校验，任何行尾转换都会让部署后的装载直接报错。

## 装配

- 主笔提示词 = `06_outline`…`08_continuity` 等分节 + `stages/draft.md`。
- `stages/draft.md` 里的 `[[FINGERPRINT]]`（`FINGERPRINT_TOKEN`）无花括号，
  可以安全穿过共享路由层的 `.format(raw_text=...)`，随后由
  `content_engine.generate_full_book` 用真实指纹替换。
- `structure_extract` 与 `differentiation_audit` 是独立阶段，
  绝不进入主笔上下文。

## 开关

```env
GENERAL_SHORT_DIFFERENTIATION_ENABLED=True   # 写指纹与审查报告（默认）
GENERAL_SHORT_DIFFERENTIATION_STRICT=False   # True 时 ≥50% 直接拦截，不产出可发布正文
```

## 产物

任务输出目录会多出两份文件：

- `00_source_fingerprint.json` — 灵感原文的结构指纹
- `05_differentiation_report.json` — 综合分、S/M 分项、判定、差异清单、改法建议

## 测试

```bash
cd backend
python -m pytest tests/test_general_short_prompts.py \
                 tests/test_general_short_fingerprint.py \
                 tests/test_general_short_differentiation.py \
                 tests/test_general_short_integration.py -q
```

覆盖：分节防污染（样稿/报告/附录六不得出现在任何阶段）、占位符穿越
`.format`、指纹抽取确定性、50% 阈值与权重冻结、端到端注入与报告落盘。
