# 任务目标
你现在是“引力奇点 (Gravity Singularity)”的首席后端架构师。你需要在一个空目录中，从零开始自主构建一个基于 FastAPI + Celery 的“视频转网文”自动化后端服务。
该系统用于向投资人进行商业 MVP 演示，必须具备极高的稳定性和工业级代码质量。

# 极其严苛的业务要求 (5000字章节墙)
客户提出硬性要求：**每个章节的字数必须达到 5000 字以上。** 由于 LLM 单次 API 请求无法稳定输出 5000 字，你必须在 Celery 任务中实现“场景切片拼接算法”来达成此目标。

# 技术栈锁定 (严格遵守)
- Web 框架: FastAPI (Python 3.11+)
- 异步队列: Celery (使用 Redis 作为 Broker 和 Backend，兼容 Upstash)
- 数据库: PostgreSQL (Supabase 托管) + SQLAlchemy (使用 psycopg2-binary 或 asyncpg)
- 请求库: httpx (用于异步 API 调用)
- 大模型 SDK: `openai` (官方 Python SDK，用于调用兼容的 DeepSeek V4 和 GPT Image API)
- 环境变量: `python-dotenv` 

# 核心流水线业务逻辑 (The AI Pipeline)
请在 Celery 任务中实现以下 5 步工作流，每完成一步，必须更新数据库中 Task 表的 `progress` (0-100) 和 `current_step` 说明：

1. **获取文案 [0-10%]**: 接收轻抖 API/RapidAPI 解析后的视频纯文本。
2. **剧本清洗与大纲生成 [10-30%]**: 
   - 调用 DeepSeek V4 (Temperature=0.2)。
   - 输出严格的 JSON，包含：《世界观设定》、《主角小传》、《分章大纲(含每章核心冲突)》。
3. **视觉海报生成 [30-40%]**: 
   - 提取“电影级视觉描述词”，调用 GPT Image API 生成竖版小说封面，存入 Task 表。
4. **【核心攻坚】5000字深水区滑动扩写 [40-90%]**: 
   - 遍历大纲的每一章，为了达到 5000 字，单章内部必须执行以下【切片循环】：
     - 步骤 A (拆解): 将本章大纲发给大模型，要求将其拆分为 4 到 5 个连贯的“子场景 (Sub-scenes)”。
     - 步骤 B (循环撰写): 遍历这 5 个子场景。每次调用 DeepSeek V4 (Temperature=0.75)，Prompt 必须包含：[全局设定] + [上一个子场景的最后 800 字作为衔接] + [当前子场景的细节指令: 强制要求丰富环境描写、主角心理博弈和动作细节]。
     - 步骤 C (字数校验与拼接): 将生成的子场景文本在 Python 内存中拼接。如果拼接后总字数不足 4500-5000 字，自动触发一个“补丁扩展请求”，让大模型针对当前章节结尾继续延伸剧情或加深心理描写。
   - 单章拼接完成后，作为一条完整记录写入 `Chapter` 表，并更新进度。
5. **完结 [100%]**: 更新 Task 状态为 `completed`。

# 数据库 Schema 要求
构建两个 SQLAlchemy 模型：
1. `Task`: id (UUID, primary_key), source_url (String), status (String), progress (Integer), current_step (String), cover_url (String), config (JSONB), created_at (DateTime)。
2. `Chapter`: id (UUID, primary_key), task_id (UUID, ForeignKey), chapter_num (Integer), title (String), content (Text), created_at (DateTime)。

# API 接口要求 (FastAPI)
必须配置 CORS 允许所有来源。
- `POST /api/v1/tasks`: 接收 `{ "url": "...", "config": {} }`，将任务压入 Celery，返回 `task_id`。
- `GET /api/v1/tasks/{task_id}`: 返回任务进度、状态、封面图。
- `GET /api/v1/tasks/{task_id}/chapters`: 返回该任务下的所有小说章节。

# Claude Code 执行步骤 (Action Plan)
请严格按照以下步骤自主执行，每完成一步请简要说明，再进行下一步：
1. **初始化**: 创建 `requirements.txt`、`.env.example`，执行安装。
2. **基建**: 编写 `database.py` 和 `models.py`。
3. **服务层**: 创建 `services.py`，封装 API 调用，特别是重点实现针对 5000 字的“大模型切片调用与字数统计拼接函数”。
4. **调度**: 创建 `celery_app.py` 和 `tasks.py`。
5. **接口**: 编写 `schemas.py` 和 `main.py`。
6. **自检**: 检查跨域、模块导入和异步函数的 await 使用是否正确。

现在，请开始执行步骤 1。