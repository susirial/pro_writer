# 多智能体小说创作与评审系统

本项目是一个基于火山引擎 VeADK（Volcengine Agent Development Kit）构建的“写作 + 多视角评审 + 强制修订 + 断点续跑”的小说创作流水线。系统通过状态机编排作家智能体、审阅专员与多位读者视角智能体，实现章节级的持续产出与质量迭代。

## 适用场景
- 需要将“题材设定/剧情规划”固化为可复现的写作计划，并持续自动写作、评审与修订
- 需要多人格读者并发评审（情节、人物、画面、爽点、主题等）
- 需要可审计、可恢复的流程（文件落盘 + 状态文件 + 显式 CLI 闸门）

## 核心能力
- 多智能体协作：Writer + Reviewer + Reader(1~5) + Orchestrator
- 状态机流程：Init → Plan → Confirm → Write → Review(并发) → 强制修订 → Next
- 文件驱动输入：需求、计划与确认均通过文件落盘，便于审计与复现
- 断点续跑：从 `output/写作任务状态.md` 恢复进度（同时避免越过确认点）
- 计划调试输出：`run` 前可打印写作计划节选辅助排障
- 角色级模型配置：支持为不同智能体分别配置 model_name/api_base/api_key/timeout/retries

## 业务流程图（ASCII）

### 0) 模型可配置（多模型混用）总览

```text
┌───────────────────────────────────────────────────────────────┐
│                    模型配置：按角色独立可配置                  │
│                                                               │
│  Writer        -> GLM-5.1 (示例：Z.ai)                         │
│  Reviewer      -> 豆包 2.0 (Ark OpenAI 网关)                   │
│  Reader_1..5   -> DeepSeek v3 / 豆包 2.0 / GLM-5.1 (可混用)     │
│  Orchestrator  -> 豆包 2.0 / DeepSeek v3 / GLM-5.1             │
│                                                               │
│  配置方式：复制 .example_env 为 .env，然后按需要替换模型/Key     │
└───────────────────────────────────────────────────────────────┘
```

### 1) 从 0 到写完（CLI + 确认点 + 断点）

```text
┌──────────────────────────┐
│          开始            │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 1) init                  │
│ python3 novel_orchestrator│
│ .py init                 │
└─────────────┬────────────┘
              │ 生成/补全
              │ - output/用户需求.md
              │ - output/写作任务状态.md
              ▼
┌──────────────────────────┐
│ 2) 填写用户需求           │
│ 编辑 output/用户需求.md   │
│ 勾选：- [x] 我已确认...   │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 3) plan                  │
│ python3 novel_orchestrator│
│ .py plan                 │
└─────────────┬────────────┘
              │ 产出
              │ - output/写作计划.md
              │ - output/写作计划确认.md
              ▼
┌──────────────────────────┐
│ 4) 确认写作计划           │
│ 编辑 output/写作计划确认.md│
│ 勾选：- [x] 我已确认...   │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 5) run                   │
│ python3 novel_orchestrator│
│ .py run                  │
│ (可选：--show-plan-lines) │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 6) 持续写作/评审/修订循环  │
│ 直到完成 total_chapters   │
└─────────────┬────────────┘
              │
              ▼
┌──────────────────────────┐
│ 7) DONE（写作完成）       │
│ state = Done             │
└──────────────────────────┘
```

### 2) 单章内部流程（写作 → 并发评审 → 强制修订 → 下一章）

```text
┌──────────────────────────────────────────────┐
│ 章节 N：WRITE                                │
│ - Writer 写正文（强绑定 用户需求 + 写作计划） │
│ - 输出：output/contents/chapterN/第N章.md     │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│ 章节 N：ASYNC_REVIEW（并发评审）              │
│ - Reviewer（去AI味/专业审阅）                 │
│ - Reader_1（情节）Reader_2（人物）            │
│ - Reader_3（画面）Reader_4（爽点）            │
│ - Reader_5（主题）                            │
│ - 输出：第N章读者反馈.md                       │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│ 章节 N：REWRITE（强制修订 1 次）               │
│ - Writer 依据评审反馈修订正文                  │
│ - 输出：覆盖写回 第N章.md                      │
└───────────────────────┬──────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│ 章节 N：NEXT_CHAPTER（推进）                   │
│ - 为 Writer 提炼本章摘要（便于后续衔接）        │
│ - N < total_chapters → N+1                    │
│ - N >= total_chapters → DONE                  │
└───────────────────────┴──────────────────────┘
```

### 3) 关键“确认点”与“完成判定”

```text
确认点 A（进入 plan 之前）：
  output/用户需求.md 中必须存在：
    - [x] 我已确认...

确认点 B（进入 run 之前）：
  output/写作计划确认.md 中必须存在：
    - [x] 我已确认...

写作完成（DONE）判定：
  当 current_chapter >= total_chapters 时
  orchestrator 将 state 置为 Done 并结束工作流
```

## 目录结构

```text
起点中文小说审核/
├── novel_orchestrator.py                 # CLI 入口（init/plan/run/review/resume）
├── config.yaml                           # 系统配置（含模型配置与提示词映射）
├── .env                                  # 本地环境变量（密钥/模型参数，勿提交）
├── core/
│   ├── orchestrator.py                   # 状态机与工作流编排
│   ├── agent_factory.py                  # 智能体工厂（按角色配置模型/工具/提示词）
│   ├── reviewer_pool.py                  # 并发评审池
│   ├── memory.py                         # 记忆管理（摘要提炼等）
│   ├── config.py                         # 配置加载、校验与环境变量注入
│   └── logger.py                         # 日志
├── tools/
│   └── robust_file_tools.py              # 供智能体调用的文件工具（读/写/列目录）
├── tests/
│   └── test_config.py                    # 配置加载与继承逻辑测试
├── 素材设定/                              # 小说设定/剧情规划（可用于填充用户需求）
└── output/                               # 运行产物（需求/计划/章节正文/评审反馈/状态文件）
```

## 环境要求
- Python 3.10+（推荐使用虚拟环境）
- 已获取可用的大模型 API Key（火山方舟 Ark 或兼容 OpenAI 格式的网关）

## 安装

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install veadk-python pyyaml
```

说明：
- 项目依赖 VeADK 运行时（底层兼容 Google ADK / LiteLLM 路由）
- 若你希望扩展更多能力（例如 connectors、工具链等），可参考 [VEADK_DEV_GUIDE.md](file:///Users/susirial/work_station/SOLO_MTC_SHOW/%E8%B5%B7%E7%82%B9%E4%B8%AD%E6%96%87%E5%B0%8F%E8%AF%B4%E5%AE%A1%E6%A0%B8/VEADK_DEV_GUIDE.md)

## 配置

### 1) 配置 `.env`（必须）
系统会读取根目录 `.env`，并将 `config.yaml` 中的 `${ENV_VAR}` 占位符解析为实际值。

建议从示例文件开始：
- 复制 [/.example_env](file:///Users/susirial/work_station/SOLO_MTC_SHOW/%E8%B5%B7%E7%82%B9%E4%B8%AD%E6%96%87%E5%B0%8F%E8%AF%B4%E5%AE%A1%E6%A0%B8/.example_env) 为 `.env`
- 再替换为你自己的 `API_KEY` 与网关地址

全局默认（所有智能体继承）：

```env
MODEL_AGENT_PROVIDER=openai
MODEL_AGENT_NAME=doubao-seed-2-0-pro-260215
MODEL_AGENT_API_BASE=https://ark.cn-beijing.volces.com/api/v3/
MODEL_AGENT_API_KEY=replace-with-your-api-key
MODEL_AGENT_TIMEOUT=60
MODEL_AGENT_MAX_RETRIES=3
```

角色级覆盖（可选）：

```env
MODEL_WRITER_NAME=...
MODEL_WRITER_API_BASE=...
MODEL_WRITER_API_KEY=...
MODEL_WRITER_TIMEOUT=60
MODEL_WRITER_MAX_RETRIES=3

MODEL_REVIEWER_NAME=...
MODEL_REVIEWER_API_BASE=...
MODEL_REVIEWER_API_KEY=...
MODEL_REVIEWER_TIMEOUT=60
MODEL_REVIEWER_MAX_RETRIES=3

MODEL_READER_NAME=...
MODEL_READER_API_BASE=...
MODEL_READER_API_KEY=...
MODEL_READER_TIMEOUT=60
MODEL_READER_MAX_RETRIES=3

MODEL_ORCHESTRATOR_NAME=...
MODEL_ORCHESTRATOR_API_BASE=...
MODEL_ORCHESTRATOR_API_KEY=...
MODEL_ORCHESTRATOR_TIMEOUT=60
MODEL_ORCHESTRATOR_MAX_RETRIES=3
```

读者 persona 级覆盖（可选）：

```env
MODEL_READER_1_NAME=...
MODEL_READER_1_API_BASE=...
MODEL_READER_1_API_KEY=...
MODEL_READER_1_TIMEOUT=60
MODEL_READER_1_MAX_RETRIES=3
```

安全建议：
- `.env` 包含密钥，建议加入版本控制忽略（不要提交到仓库）
- 如果密钥已泄露，立刻在控制台轮换并替换 `.env`

### 2) 配置 `config.yaml`（一般无需改）
- 模型默认值与各角色覆盖字段位于 `model.agent` 与 `models.*`
- 提示词映射位于 `prompts.*`

## 快速开始（推荐流程）

本系统采用“文件驱动输入”，需求不是在命令行交互输入，而是写入并确认 `output/用户需求.md`。

```text
init -> 填写并确认 用户需求.md -> plan -> 确认 写作计划确认.md -> run
```

### 1) 初始化（不调用模型）

```bash
python3 novel_orchestrator.py init
```

会生成/更新：
- `output/用户需求.md`（模板）
- `output/写作任务状态.md`（状态文件）

### 2) 填写并确认用户需求

编辑 `output/用户需求.md` 的“需求内容”，并在最后将：

```text
- [ ] 我已确认以上需求无误（将 [ ] 改为 [x]）
```

改为：

```text
- [x] 我已确认以上需求无误（将 [ ] 改为 [x]）
```

你也可以参考 `素材设定/` 目录，将小说要求与剧情规划整理到 `用户需求.md` 中。

### 3) 生成写作计划（Plan）

```bash
python3 novel_orchestrator.py plan
```

输出：
- `output/写作计划.md`
- `output/写作计划确认.md`

此时系统停在确认点，不会写正文。

### 4) 确认写作计划

编辑 `output/写作计划确认.md`，将确认项改为 `[x]`。

### 5) 启动写作流水线（Run）

```bash
python3 novel_orchestrator.py run
```

调试建议：
- 默认会在运行前打印写作计划前 40 行节选（用于确认当前写作计划版本）
- 可显式指定节选行数：

```bash
python3 novel_orchestrator.py run --show-plan-lines 80
```

或关闭计划节选打印：

```bash
python3 novel_orchestrator.py run --no-show-plan
```

## CLI 子命令

```bash
python3 novel_orchestrator.py init
python3 novel_orchestrator.py plan
python3 novel_orchestrator.py run
python3 novel_orchestrator.py review --chapter 3
python3 novel_orchestrator.py resume
```

说明：
- `review --chapter N`：仅对指定章节执行评审与决策（不写新正文）
- `resume`：从状态文件断点续跑（若处于确认点会停住并提示）

## 输出文件说明

```text
output/
├── 用户需求.md
├── 写作计划.md
├── 写作计划确认.md
├── 写作任务状态.md
└── contents/
    └── chapterN/
        ├── 第N章.md
        ├── 第N章读者反馈.md
        └── 第N章是否通过审核.md
```

## 工作流细节（重要）

### 1) 计划与正文的一致性
为了避免“正文跑偏”，系统在写正文/修订时会将：
- `output/用户需求.md`
- `output/写作计划.md` 的关键节选（基础设定 + 当前弧信息）

注入到 Writer 的写作/修订提示词中，约束题材、主角与世界观不被模型更换。

### 2) 评审后强制修订一次
每章完成并发评审（审阅专员 + 读者1~5）后，系统会强制执行一次修订，然后才进入下一章（避免“通过就直接下一章”导致的问题累积）。

### 3) 断点与残留
系统是“产物落盘 + 状态断点续跑”模式：
- 更新 `写作计划.md` 不会自动重写历史 `output/contents/` 章节文件
- 开新项目建议清理 `output/contents/` 与 `output/写作任务状态.md`（或采用隔离目录策略）

## 常见问题排查

### 1) 计划/正文不一致
- 确认 `run` 前打印的“写作计划节选”是否为你期望的版本
- 检查 `output/写作任务状态.md` 的 `current_chapter/state` 是否是旧断点续跑
- 如需从 1 开始：清理 `output/contents/` 与 `output/写作任务状态.md` 后重新走流程

### 2) 报错 “Model ... not found”
- 确认 `.env` 中 `MODEL_*_NAME` 是否为当前网关支持的模型名
- 若使用 Ark OpenAI 网关：确认 `MODEL_*_API_BASE` 与 `MODEL_*_API_KEY` 正确

### 3) 报错 “章节文件不存在”
通常是状态处于 `ASYNC_REVIEW`，但对应 `第N章.md` 未生成或被删除。可将状态回退到 `WRITE` 再运行 `resume/run`。

## 开发与测试

运行单元测试：

```bash
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

（当前测试主要覆盖 `core/config.py` 的环境变量优先级与继承逻辑）
