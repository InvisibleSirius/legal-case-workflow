# 法律案件与文书工作流 Skill

一个面向中国法律案件材料处理与法律文书交付的本地 Codex Skill。它将案件证据整理、法律文书生成和 DOCX 格式验收整合为一个显式调用的工作流。

> 当前版本：`v1.0.0`  
> 仓库状态：私有仓库  
> 调用方式：`$legal-case-workflow`

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
└── scripts/                       # 材料接收、案件验收和 DOCX 验收脚本
```

## 安装

在目标法律工作区根目录执行：

```bash
git clone https://github.com/InvisibleSirius/legal-case-workflow.git \
  .agents/skills/legal-case-workflow
```

确保工作区规则要求只有用户显式调用 `$legal-case-workflow` 才启动完整案件流程。

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
```

按版本画像检查 DOCX 文书包：

```bash
python scripts/validate_document_package.py \
  --package-dir <交付目录> \
  --profile references/profiles/qintong-enforcement-human-v20260828.json \
  --final
```

自动检查不能替代 Word、WPS 或等效工具中的逐页视觉检查。

## 证据与隐私边界

- 本仓库不包含任何案件原始材料、当事人文书或案件输出文件。
- `references/firm-constants.md` 含特定律所的内部格式基线，仅适合受控使用。
- 不得将版本画像中的当事人、案号、金额、联系方式或代理选择复制到其他案件。
- 如需将仓库改为公开，应先移除或抽象律所名称、地址、统一社会信用代码和人员样本信息。

## 版本说明

本版本将原独立的法律文书排版规则并入案件工作流，形成“案件证据—文书制作—格式验收”三层结构。详细变化见 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)。

## 许可

当前私有仓库未附开源许可证。未经仓库所有者明确许可，不得公开分发或改为公开仓库。
