# 跨平台兼容与调用协议

## 1. 共用核心

Codex、Kimi Code、WorkBuddy 与 CodeBuddy Code 共用同一份 `SKILL.md`、`scripts/`、`references/` 和 `assets/`。不得为不同平台复制或分叉案件事实规则、文书格式规则和验收门。

平台差异只允许出现在以下三处：

1. Skill 的发现或导入方式；
2. 用户显式调用语法；
3. 当前平台提供的等价文件、命令、渲染和授权工具。

## 2. Codex

- 项目级目录：`.agents/skills/legal-case-workflow/` 或 `.codex/skills/legal-case-workflow/`
- 用户级目录：`~/.agents/skills/legal-case-workflow/` 或 `~/.codex/skills/legal-case-workflow/`
- 显式调用：`$legal-case-workflow`
- `agents/openai.yaml` 只用于 Codex 展示和显式调用控制；其他平台可以安全忽略。

安装或更新后，应在新任务中调用，避免旧对话继续使用已缓存的旧版本说明。

## 3. Kimi Code

- 推荐用户级目录：`~/.kimi/skills/legal-case-workflow/`
- 推荐项目级目录：`.agents/skills/legal-case-workflow/` 或 `.kimi/skills/legal-case-workflow/`
- 显式调用：`/skill:legal-case-workflow`
- 无内置命令冲突时，可使用简写：`/legal-case-workflow`

Kimi Code 默认还可发现已有的 `~/.codex/skills/`；若机器上存在多个同名副本，以 Kimi 的发现优先级为准，并保证只维护一个权威版本。

本工作流保持普通 Agent Skill 形式，不改为 Kimi Flow Skill。案件处理中存在材料缺口、人工核对、签署状态和视觉验收等停止条件，不适合无条件自动跨轮执行。

## 4. WorkBuddy

使用 `scripts/package_skill.py` 生成可导入压缩包。压缩包根目录必须直接包含 `SKILL.md`，同时保留 `scripts/`、`references/` 和 `assets/` 的相对路径。

安装步骤：

1. 在 WorkBuddy 左侧进入“专家·技能·连接器”；
2. 选择“添加技能”或“上传技能”；
3. 导入生成的 ZIP；
4. 确认技能已启用；
5. 在对话中明确说“使用 legal-case-workflow 处理 cases/...”。

WorkBuddy 会根据描述自动匹配已启用 Skill，因此 `description` 与正文都保留“仅显式点名后启动”的边界。仅上传材料或提出一般法律问题时，不得自行建立案件目录或启动完整流程。

涉及本地案件材料时，只授权当前案件目录所需的读写范围。Skill 加载成功只证明说明文件可用，不证明 Python 脚本、DOCX 渲染器或外部依赖已经可执行。

## 5. CodeBuddy Code

若使用 WorkBuddy Enterprise 中的 CodeBuddy Code：

- 用户级目录：`~/.codebuddy/skills/legal-case-workflow/`
- 项目级目录：`.codebuddy/skills/legal-case-workflow/`
- 显式调用：`/legal-case-workflow`

CodeBuddy 支持 `allowed-tools`，但本共用 Skill 不在 frontmatter 中写入平台专有工具名，以免破坏其他运行时兼容性。权限由当前工作区和用户授权控制。

## 6. 通用打包

在仓库根目录运行：

```bash
python3 scripts/package_skill.py
```

默认输出：

```text
dist/legal-case-workflow-v<版本号>-universal.zip
```

压缩包内额外包含 `MANIFEST.sha256`，用于核对打包文件是否完整。发布前至少检查：

1. ZIP 根目录存在 `SKILL.md`；
2. `SKILL.md` 的 `name` 与目录名一致；
3. 所有相对引用文件存在；
4. 三个业务脚本的 `--self-test` 通过；
5. ZIP 解压后重新运行 Skill 结构检查。

## 7. 平台降级原则

- 没有自动 DOCX 渲染能力：保留结构检查结果，并明确标记视觉验收未完成，不得声称可直接交付。
- 没有 Python 或所需依赖：记录具体失败项；可以继续不依赖该脚本的材料登记、人工核对和文书审阅。
- 没有外部检索能力：只处理本地材料；不得凭记忆补写法律依据或案件事实。
- 没有子代理能力：由主智能体顺序完成；不得降低事实合并和最终定稿责任。
- 平台工具名称不同：按能力映射，不把 Codex、Kimi 或 WorkBuddy 的专有命令写进案件交付文件。
