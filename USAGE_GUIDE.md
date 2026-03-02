# 长时间运行 Agent 编排系统 - 详细使用指南

## 目录

- [系统工作原理](#系统工作原理)
- [前置准备](#前置准备)
- [场景一：从零构建全新项目](#场景一从零构建全新项目)
- [场景二：为已有项目添加新功能](#场景二为已有项目添加新功能)
- [运行参数详解](#运行参数详解)
- [核心文件解读](#核心文件解读)
- [运行过程中的人工干预](#运行过程中的人工干预)
- [常见问题与排查](#常见问题与排查)

---

## 系统工作原理

本系统解决的核心问题是：**AI Agent 的每个会话都是独立的上下文窗口，会话之间没有记忆**。

系统通过三种结构化工件在多个会话间传递上下文：

```
┌──────────────────────────────────────────────────────────┐
│                    编排器主循环                            │
│                                                          │
│   ┌─────────────┐   读取    ┌──────────────────────┐     │
│   │ features.json│ ◄─────── │  编码 Agent (会话 N)  │     │
│   │  功能清单     │ ──写入──►│                      │     │
│   └─────────────┘   passes  │  1. 读取三个工件      │     │
│                              │  2. 选择一个功能      │     │
│   ┌─────────────┐   读取    │  3. 写代码实现        │     │
│   │ claude-     │ ◄─────── │  4. 验证功能          │     │
│   │ progress.txt│ ──追加──►│  5. git commit        │     │
│   │  进度日志    │           │  6. 更新工件          │     │
│   └─────────────┘           └──────────────────────┘     │
│                                                          │
│   ┌─────────────┐                                        │
│   │   git 历史   │  ◄── 每个功能一次提交                   │
│   └─────────────┘                                        │
│                                                          │
│   编排器检查: 功能通过了吗?                                 │
│     ├─ 是 → 选择下一个功能，启动新会话                      │
│     ├─ 否 → 重试（最多 3 次，然后跳过）                     │
│     └─ 全部完成 → 输出最终报告                              │
└──────────────────────────────────────────────────────────┘
```

**三个 Agent 角色分工：**

| Agent | 何时运行 | 职责 |
|-------|---------|------|
| 初始化 Agent | 仅第一个会话 | 搭建项目骨架、生成功能清单、创建环境脚本、初始化 git |
| 编码 Agent | 会话 2 到 N | 每个会话实现一个功能：读取上下文 → 编码 → 验证 → 提交 |
| 验证 Agent | 最后（可选）| 逐一回归测试所有已完成功能，将失败的标记回 false |

---

## 前置准备

### 1. 安装依赖

```bash
# 进入项目目录
cd "d:/Codes/long-running agents"

# 安装 Python 依赖
pip install claude-agent-sdk pydantic pyyaml rich

# （可选）如需浏览器自动化测试
pip install playwright
npx playwright install
```

### 2. 配置认证（二选一）

`claude-agent-sdk` 底层调用 Claude Code CLI，支持两种认证方式：

#### 方式 A：使用 Claude Pro/Max 订阅（推荐）

如果你已有 Claude Pro 或 Max 订阅，直接通过 CLI 登录即可，**无需 API Key**：

```bash
# 登录你的 Anthropic 账号（会打开浏览器进行 OAuth 认证）
claude login

# 验证登录状态
claude whoami
```

登录成功后，认证信息保存在 `~/.claude/.credentials.json`，SDK 会自动读取。
此方式使用订阅额度，不产生额外 API 费用。

> **注意**：订阅方式受订阅计划的速率限制。Max 5x 订阅的限额最高。
> `--budget` 参数在订阅模式下不生效（因为不按 API 计费）。

#### 方式 B：使用 API Key

如果你使用 Anthropic API 付费账户，设置环境变量：

```bash
# Linux/Mac
export ANTHROPIC_API_KEY="sk-ant-xxxxx"

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-xxxxx"

# Windows CMD
set ANTHROPIC_API_KEY=sk-ant-xxxxx
```

此方式按 API 调用量计费，可通过 `--budget` 参数控制花费上限。

#### 认证优先级

当两种认证同时存在时，系统优先使用 **API Key**。
如果只想用订阅，确保 `ANTHROPIC_API_KEY` 环境变量未设置。

### 3. 确认 Node.js 环境（如果构建 Web 项目）

```bash
node --version   # 需要 18+
npm --version
```

---

## 场景一：从零构建全新项目

> 适用于：你有一个项目想法，想让 Agent 自动从零开始构建整个应用。

### 第一步：编写项目规格文件 (YAML)

在 `specs/` 目录下创建一个 `.yaml` 文件，这是整个系统的输入。

**文件格式：**

```yaml
# specs/my-project.yaml

# ===== 必填字段 =====

name: my-awesome-app          # 项目名（会成为输出目录名）

description: |                  # 项目描述（给 Agent 的高层背景）
  用一段话描述你的项目是什么，
  解决什么问题，面向什么用户。
  写得越清楚，Agent 的理解越准确。

tech_stack:                     # 技术栈列表
  - react                       # Agent 会根据这些选择脚手架和依赖
  - typescript
  - tailwind
  - express
  - sqlite

requirements:                   # 功能需求列表（最关键的部分！）
  - 用户注册和登录功能            # 每一条都会被转化为 features.json 中的功能项
  - 创建和编辑文章               # 写得越具体，Agent 拆分功能越准确
  - 文章列表展示和分页            # 建议 8-20 条需求
  - 文章详情页面
  - 评论功能
  - 响应式 UI 布局

# ===== 可选字段 =====

dev_server_command: "npm run dev"  # 默认 "npm run dev"
port: 3000                         # 默认 3000
test_command: "npm test"           # 默认 null

extra_instructions: |              # 给 Agent 的额外指令
  使用 Vite 而非 CRA。
  数据库用 SQLite，文件放 data/ 目录。
  API 路由前缀用 /api/v1。
```

**编写 requirements 的技巧：**

```yaml
# ❌ 太模糊 — Agent 不知道具体做什么
requirements:
  - 用户系统
  - 好看的界面

# ✅ 具体清晰 — Agent 可以拆分为明确的功能项
requirements:
  - 用户注册（邮箱、用户名、密码），密码用 bcrypt 加密
  - 用户登录，JWT token 认证
  - 退出登录，清除 token
  - 创建新文章（标题、正文、标签）
  - 文章列表页，每页 10 篇，支持翻页
  - 文章详情页，显示标题、正文、作者、发布时间
  - 响应式布局，适配手机和桌面
```

### 第二步：运行编排器

```bash
# 基本用法 — 使用默认设置
python -m src.main run specs/my-project.yaml

# 指定输出目录
python -m src.main run specs/my-project.yaml --project-dir ./output/my-app

# 使用更强的模型（效果更好但更贵）
python -m src.main run specs/my-project.yaml --model claude-opus-4-6

# 启用浏览器自动化测试（推荐用于 Web 项目）
python -m src.main run specs/my-project.yaml --playwright

# 限制预算
python -m src.main run specs/my-project.yaml --budget 10.0

# 先试运行，看看会发生什么（不花钱）
python -m src.main run specs/my-project.yaml --dry-run
```

### 第三步：系统自动执行

启动后，你会看到类似以下的输出：

```
╭──── 启动 ────╮
│ 长时间运行 Agent 编排器       │
│ 项目: my-awesome-app         │
│ 模型: claude-sonnet-4-6      │
│ 最大会话数: 50               │
╰──────────────╯

阶段 1：项目初始化
  [初始化] 正在根据规格创建 React + Express 项目...
  [工具] Bash: npx create-vite my-awesome-app --template react-ts
  [工具] Write: features.json (25 个功能项)
  [工具] Write: init.sh
  [工具] Bash: git init && git add -A && git commit -m "Initial setup"
  初始化成功: 项目初始化完成

阶段 2：功能实现

  进度: 0/25 功能已完成 (0%)
  会话 2: 功能 #1 - 项目基础配置和依赖安装
  [编码] 正在配置 Vite、TypeScript、Tailwind...
    功能 #1 已通过 ✓

  进度: 1/25 功能已完成 (4%)
  会话 3: 功能 #2 - Express 后端基础框架
  [编码] 正在创建 Express 服务器、路由结构...
    功能 #2 已通过 ✓

  进度: 2/25 功能已完成 (8%)
  会话 4: 功能 #3 - SQLite 数据库模型
  ...（持续运行，每个功能一个会话）...

  所有功能已完成！

╭──── 最终报告 ────╮
│ 总会话数:   26       │
│ 成功会话:   25       │
│ 通过功能数: 24       │
│ 功能进度:   24/25    │
│ 总耗时:     45 分钟  │
╰──────────────────╯
```

### 第四步：检查结果

```bash
# 查看功能完成状态
python -m src.main status ./output/my-awesome-app

# 进入项目目录，手动验证
cd ./output/my-awesome-app
npm run dev
```

### 第五步：中断与恢复

如果中途因为网络、预算或其他原因中断了：

```bash
# 恢复运行 — 会自动从上次停下的功能继续
python -m src.main resume ./output/my-awesome-app --spec specs/my-project.yaml
```

恢复原理：编排器检查 `features.json` 中哪些功能的 `passes` 还是 `false`，
从优先级最高的未完成功能继续。

---

## 场景二：为已有项目添加新功能

> 适用于：你有一个正在开发的项目，想让 Agent 帮你批量实现一批新功能。

这个场景需要几个额外步骤，因为系统原本是为从零开始设计的。
核心思路是：**手动准备好编排器需要的三个工件文件，然后让编码 Agent 接管**。

### 第一步：准备你的项目

确保项目已有 git 仓库：

```bash
cd /path/to/your-existing-project
git status  # 确认是 git 仓库
git log --oneline -5  # 确认有提交历史
```

### 第二步：编写项目规格文件

和新项目一样创建 YAML，但 `requirements` 只写**新增功能**：

```yaml
# specs/my-existing-project-v2.yaml

name: my-existing-project
description: |
  这是一个已有的博客系统，使用 Next.js + PostgreSQL。
  当前已实现：用户注册/登录、文章 CRUD、评论功能。
  现在需要添加以下新功能。

tech_stack:
  - nextjs
  - typescript
  - postgresql
  - prisma
  - tailwind

requirements:
  # 只写新增需求！不要重复已有功能
  - 文章标签系统（创建标签、为文章添加标签、按标签筛选）
  - 文章搜索功能（全文搜索，支持标题和内容）
  - 用户个人主页（显示用户发布的所有文章）
  - 文章收藏功能（收藏、取消收藏、查看收藏列表）
  - 站内消息通知（评论通知、收藏通知）
  - 深色模式切换

dev_server_command: "npm run dev"
port: 3000

extra_instructions: |
  这是一个已有项目，请不要修改现有功能。
  数据库迁移使用 prisma migrate。
  新路由遵循 Next.js App Router 约定。
  现有的代码风格是：组件用 PascalCase，工具函数用 camelCase。
  在实现新功能前，先阅读 src/ 目录了解现有代码结构。
```

### 第三步：手动创建 features.json

对于已有项目，你需要**手动编写**功能清单，而不是让初始化 Agent 生成
（因为初始化 Agent 会尝试从零搭建项目）。

在你的项目根目录下创建 `features.json`：

```json
[
  {
    "id": 1,
    "category": "core",
    "priority": 1,
    "description": "文章标签数据模型：创建 Tag 模型和 Article-Tag 多对多关系",
    "steps": [
      "运行 prisma migrate 创建标签表",
      "验证数据库中存在 Tag 表和关联表",
      "验证现有文章功能不受影响"
    ],
    "passes": false,
    "skipped": false
  },
  {
    "id": 2,
    "category": "core",
    "priority": 2,
    "description": "标签 CRUD API：创建、查询、删除标签的 API 端点",
    "steps": [
      "POST /api/tags 能创建新标签",
      "GET /api/tags 能列出所有标签",
      "DELETE /api/tags/:id 能删除标签"
    ],
    "passes": false,
    "skipped": false
  },
  {
    "id": 3,
    "category": "ui",
    "priority": 3,
    "description": "文章编辑器中添加标签选择器",
    "steps": [
      "文章创建页面显示标签多选组件",
      "能搜索和选择已有标签",
      "保存文章时标签关联正确写入数据库"
    ],
    "passes": false,
    "skipped": false
  },
  {
    "id": 4,
    "category": "ui",
    "priority": 4,
    "description": "按标签筛选文章列表",
    "steps": [
      "文章列表页面显示标签过滤栏",
      "点击标签后只显示含该标签的文章",
      "清除筛选后恢复完整列表"
    ],
    "passes": false,
    "skipped": false
  }
]
```

**编写 features.json 的原则：**

1. **每个功能足够小** — 一个 Agent 会话（约 30 轮工具调用）能完成
2. **按依赖排序** — priority 数字小的先做，被依赖的排在前面
3. **steps 要具体** — 写成另一个工程师能照着验证的步骤
4. **不要包含已有功能** — 只写新增的

### 第四步：手动创建 claude-progress.txt

```text
# my-existing-project - Agent 进度跟踪
# 此文件记录每个 Agent 会话的工作内容。

=== 项目背景 ===
这是一个已有项目。当前已实现的功能包括：
- 用户注册/登录（JWT 认证）
- 文章 CRUD（创建、读取、更新、删除）
- 评论功能
- 基础 UI 布局

数据库：PostgreSQL，ORM 使用 Prisma
前端：Next.js App Router + Tailwind CSS
代码结构：
  src/app/          - 页面路由
  src/components/   - React 组件
  src/lib/          - 工具函数和数据库客户端
  prisma/schema.prisma - 数据库模型

=== 会话 0 (手动准备) ===
功能：环境准备
状态：已完成
变更：创建 features.json（4 个新功能）和此进度文件
Git 提交：无（手动创建）
```

**关键点**：在进度文件中详细描述项目现状，让编码 Agent 能快速理解代码库。

### 第五步：手动创建 init.sh

```bash
#!/bin/bash
# 安装依赖
npm install 2>/dev/null || true

# 运行数据库迁移
npx prisma migrate deploy 2>/dev/null || true

# 启动开发服务器
npm run dev &
echo "开发服务器已在端口 3000 启动"
```

### 第六步：确认工件文件就位

```
your-existing-project/
├── features.json           ← 新增功能清单
├── claude-progress.txt     ← 进度文件（含项目背景）
├── init.sh                 ← 环境启动脚本
├── .git/                   ← 已有 git 仓库
├── src/                    ← 已有代码
├── package.json
└── ...
```

### 第七步：运行编排器

```bash
# 注意：--project-dir 指向你的已有项目目录
python -m src.main resume /path/to/your-existing-project \
  --spec specs/my-existing-project-v2.yaml \
  --model claude-sonnet-4-6
```

使用 `resume` 而非 `run`，因为 `run` 检测到已有 `features.json` 和 `.git` 后
会跳过初始化阶段，直接进入功能实现循环。效果等同，但语义更清晰。

编排器会：
1. 检测到项目已初始化（`.git`、`features.json` 等都存在），跳过初始化
2. 读取 `features.json`，找到第一个 `passes: false` 的功能
3. 启动编码 Agent，Agent 会先读取 `claude-progress.txt` 了解项目背景
4. Agent 阅读现有代码，在此基础上实现新功能
5. 完成后更新 `features.json` 和进度文件
6. 重复直到所有功能完成

---

## 运行参数详解

### 三个命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `run` | 从规格文件启动新项目 | `python -m src.main run specs/app.yaml` |
| `status` | 查看功能完成进度 | `python -m src.main status ./output/app` |
| `resume` | 从中断处恢复运行 | `python -m src.main resume ./output/app --spec specs/app.yaml` |

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--model` | `claude-sonnet-4-6` | 使用的 Claude 模型。`opus` 更强但更贵，`sonnet` 性价比高 |
| `--max-sessions` | `50` | 安全上限，防止无限循环消耗 API 额度 |
| `--max-turns` | `30` | 每个会话最大工具调用轮次 |
| `--budget` | 无限制 | 总花费上限（美元），达到后停止 |
| `--playwright` | 关闭 | 启用后 Agent 可以打开浏览器验证 UI 功能 |
| `--dry-run` | 关闭 | 试运行，展示执行计划但不调用 Claude |
| `--verbose` / `-v` | 关闭 | 显示详细日志（工具调用参数等） |
| `--project-dir` | `./output/<name>` | 项目输出目录（仅 `run` 命令） |

### 模型选择建议

| 模型 | 适用场景 | 每个会话约成本 |
|------|---------|---------------|
| `claude-haiku-4-5` | 简单功能、纯前端、低预算 | ~$0.05 |
| `claude-sonnet-4-6` | 大多数场景（推荐默认） | ~$0.30 |
| `claude-opus-4-6` | 复杂架构、调试困难的功能 | ~$1.50 |

---

## 核心文件解读

### features.json — 功能清单（最重要的文件）

这是整个系统的"路线图"。每个功能项的字段含义：

```json
{
  "id": 1,              // 唯一标识符
  "category": "core",   // 类别：setup/core/ui/integration/polish
  "priority": 1,        // 优先级（数字越小越先做）
  "description": "...", // 功能描述（Agent 据此理解要做什么）
  "steps": ["..."],     // 验证步骤（Agent 据此判断功能是否完成）
  "passes": false,      // 是否已通过验证 ← Agent 唯一可以修改的字段
  "skipped": false       // 是否已跳过（连续失败 3 次时自动标记）
}
```

**安全机制**：系统通过 SDK 钩子在工具层面强制执行——如果 Agent 试图修改
`description`、`steps`、`category` 等字段，写入操作会被拦截并拒绝。
这防止了 Agent 为了"通过"而降低验收标准。

### claude-progress.txt — 会话交接文档

每个新会话开始时，Agent 首先阅读此文件了解：
- 项目整体背景（对已有项目特别重要）
- 之前的会话做了什么
- 遇到了什么问题

格式示例：
```
=== 会话 5 (2026-03-02 15:30) ===
功能：用户登录表单
状态：已完成
变更：创建 LoginForm 组件、auth API 路由
Git 提交：a1b2c3d
```

### init.sh — 环境启动脚本

Agent 可以运行此脚本快速启动开发环境，无需每次都手动配置。
必须是**幂等的**（运行多次不会出错）。

---

## 运行过程中的人工干预

### 查看实时进度

在另一个终端窗口：

```bash
# 查看功能完成状态
python -m src.main status ./output/my-app

# 查看进度文件
cat ./output/my-app/claude-progress.txt

# 查看 git 提交历史
cd ./output/my-app && git log --oneline
```

### 手动修正 features.json

如果 Agent 把某个功能标记为通过但实际没做好，你可以手动修改：

```json
// 改回 false，编排器恢复运行时会重新尝试这个功能
{"id": 5, "passes": false, ...}
```

如果某个功能不想做了，手动标记跳过：

```json
{"id": 5, "skipped": true, ...}
```

### 手动添加进度备注

在 `claude-progress.txt` 末尾追加人工备注，下一个 Agent 会话会读到：

```
=== 人工备注 (2026-03-02 16:00) ===
注意：数据库连接字符串已从环境变量改为 .env 文件。
新的 Agent 会话请先读取 .env.example 了解配置。
```

### 中途停止

直接 `Ctrl+C` 终止程序。不会丢失已完成的进度，因为：
- 每个功能完成后立即 git commit
- features.json 实时更新
- 下次 resume 从断点继续

---

## 常见问题与排查

### Q: 同一个功能反复失败怎么办？

系统内置停滞检测：连续 3 次失败会自动跳过。但如果你想手动处理：

1. 查看 `claude-progress.txt` 中的失败记录，理解失败原因
2. 手动修复代码中的阻塞问题
3. 在 `claude-progress.txt` 中追加备注说明你做了什么修复
4. 运行 `resume` 继续

### Q: Agent 生成的 features.json 功能太少/太多怎么办？

初始化 Agent 生成 `features.json` 后，在启动编码循环前，你可以手动编辑：
- 添加遗漏的功能项
- 删除不需要的功能项
- 调整优先级
- 细化验证步骤

### Q: 如何控制代码质量？

1. 在 `extra_instructions` 中写明代码规范
2. 使用 `--playwright` 启用浏览器测试
3. 运行结束后审查 `git log`，必要时回滚
4. 使用验证 Agent 做最终回归测试

### Q: 预算估算

粗略估算：
- **每个功能** ≈ 1 个会话 ≈ 10-30 轮工具调用
- **Sonnet 模型**：每个功能约 $0.20-$0.50
- **20 个功能的项目**：约 $5-$15
- 使用 `--budget` 参数设置硬上限

### Q: 支持哪些类型的项目？

系统不限定项目类型。只要你的规格文件能清楚描述，Agent 可以构建：
- Web 应用（React、Vue、Next.js、Express 等）
- CLI 工具（Python、Node.js、Rust 等）
- API 服务（REST、GraphQL）
- 移动端项目（React Native）
- 桌面应用（Electron、Tauri）
- 任何有 dev server 或构建命令的项目
