# 法律案件与文书工作流 Skill

一个面向中国法律案件材料处理与法律文书交付的跨智能体 Skill。它将案件证据整理、法律文书生成和 DOCX 格式验收整合为一个显式调用的工作流，可用于 Codex、Kimi Code、WorkBuddy 和 CodeBuddy Code。

> 当前版本：`v1.1.0`
> 仓库状态：公开发布  
> 核心格式：Agent Skills（`SKILL.md`）

## 主要能力

### 1. 案件证据层

- 原始材料登记、文件类型识别和 SHA-256 记录；
- OCR、文本提取和可搜索材料只作为检索辅助；
- 建立时间线、事实矩阵、证据矩阵、争点和矛盾清单；
- 区分已核事实、当事人陈述、合理推断、争议事实和待核事项；
- 关键事实可回溯至材料编号、PDF 页码或截图坐标。

### 2. 法律文书层

- 按用户要求生成或修订中国法律文书 DOCX；
- 支持 `requested-only`、`filing-core` 和 `filing-complete` 三种文书包范围；
- 按“用户要求—机关模板—批准模板—版本画像—通用基线”选择格式权威；
- 默认采用最小填充模式，避免未经授权删除条款、改动表格或重编号；
- 将人工修正版的排版特征保存为可选版本画像，而不是通用硬编码规则。

### 3. 格式与交付验收层

- 检查 DOCX 压缩包和 XML 结构；
- 检查表格行列、页数属性、字号、字体、图片和必要文字；
- 清理正式终稿中的内部占位语和处理过程语言；
- 执行跨文书主体、案号、金额、权限、日期和页数一致性检查；
- 要求逐页视觉检查、回滚版本和最终文件哈希。

## 工作模式

| 模式 | 用途 |
|---|---|
| `intake-fast` | 快速登记原始材料和处理状态 |
| `analysis-focused` | 制作时间线、事实/证据矩阵和争点清单 |
| `document-package` | 生成、修订并验收法律文书包 |
| `hearing-prep` | 制作庭审提纲与证据缺口清单 |
| `deep-review` | 对高风险字段和跨文书一致性进行深度复核 |
| `update-incremental` | 仅处理新增或哈希发生变化的材料 |

## 目录说明

```text
legal-case-workflow/
├── SKILL.md                       # Skill 主入口与工作流规则
├── agents/openai.yaml             # Codex 展示信息及显式调用策略
├── assets/templates/              # 案件索引、事实矩阵、交接等模板
├── references/                    # 证据、文书、子代理及律所字段协议
│   └── profiles/                  # 可选的版本化格式画像
└── scripts/                       # 材料接收、案件验收、DOCX 验收和通用打包脚本
```

## 平台兼容性

| 平台 | 安装或导入方式 | 显式调用 |
|---|---|---|
| Codex | 放入 `.agents/skills/`、`.codex/skills/` 或对应用户级目录 | `$legal-case-workflow` |
| Kimi Code | 放入 `.kimi/skills/`、`.agents/skills/`；默认也可发现已有 `~/.codex/skills/` | `/skill:legal-case-workflow` |
| WorkBuddy | 在技能页面上传本仓库生成的通用 ZIP 并启用 | 明确说“使用 legal-case-workflow 处理……” |
| CodeBuddy Code | 放入 `.codebuddy/skills/` 或 `~/.codebuddy/skills/` | `/legal-case-workflow` |

本仓库保持一份事实、证据、格式和验收规则。各平台只适配入口与工具能力，不维护互相漂移的分叉版本。详细说明见 [跨平台兼容与调用协议](references/platform-compatibility.md)。

## 安装与调用

### Codex

在目标法律工作区根目录执行：

```bash
git clone https://github.com/InvisibleSirius/legal-case-workflow.git \
  .agents/skills/legal-case-workflow
```

确保工作区规则要求只有用户显式调用 `$legal-case-workflow` 才启动完整案件流程。

### Kimi Code

用户级安装：

```bash
git clone https://github.com/InvisibleSirius/legal-case-workflow.git \
  ~/.kimi/skills/legal-case-workflow
```

项目级也可安装到 `.agents/skills/legal-case-workflow`。调用示例：

```text
/skill:legal-case-workflow 对 cases/CASE-2026-001 执行 analysis-focused 模式
```

### WorkBuddy

先在仓库根目录生成通用包：

```bash
python3 scripts/package_skill.py
```

然后在 WorkBuddy 的技能页面选择“上传技能”，导入 `dist/` 下的 ZIP 并启用。压缩包根目录已直接放置 `SKILL.md`，适合本地技能包导入。

### CodeBuddy Code

将仓库放到项目的 `.codebuddy/skills/legal-case-workflow/`，或用户目录的 `~/.codebuddy/skills/legal-case-workflow/`，再用 `/legal-case-workflow` 调用。

## 使用示例

```text
$legal-case-workflow
请对 cases/CASE-2026-001-示例案件执行 document-package 模式，
只制作我列出的文书，并按指定模板完成结构与视觉验收。
```

## 自动检查

运行三个脚本的内置测试：

```bash
python scripts/case_intake.py --self-test
python scripts/validate_case.py --self-test
python scripts/validate_document_package.py --self-test
python scripts/package_skill.py --self-test
```

按版本画像检查 DOCX 文书包：

```bash
python scripts/validate_document_package.py \
  --package-dir <交付目录> \
  --profile references/profiles/qintong-enforcement-human-v20260828.json \
  --final
```

自动检查不能替代 Word、WPS 或等效工具中的逐页视觉检查。

## 证据、隐私与公开边界

- 本仓库不包含任何案件原始材料、当事人文书或案件输出文件。
- `references/firm-constants.md` 含特定律所的格式基线，包括律所名称、统一社会信用代码、负责人、地址和样本律师姓名；这些内容随本仓库公开。
- 不得将版本画像中的当事人、案号、金额、联系方式或代理选择复制到其他案件。
- 使用者应将律所字段和版本画像视为示例性配置，在应用于其他机构或案件前完成替换和事实核验。

## 版本说明

`v1.1.0` 在原“案件证据—文书制作—格式验收”三层结构上增加 Kimi Code、WorkBuddy 与 CodeBuddy Code 的发现、调用和打包兼容层。详细变化见 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)。

平台依据：[Kimi Code Agent Skills](https://github.com/MoonshotAI/kimi-cli/blob/main/docs/en/customization/skills.md)、[WorkBuddy 技能](https://cloud.tencent.com/document/product/1831/134432)、[CodeBuddy Skills](https://cloud.tencent.com/document/product/1831/134516)。

## 许可

本仓库虽可公开访问，但当前未附开源许可证。公开可见不等于授权复制、修改或再分发；相关权利由仓库所有者保留。
