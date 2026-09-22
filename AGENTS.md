# AIGC 短剧工作台 — 项目指南

> 个人 Demo：AI 辅助短剧剧本生成工作流。供本仓库内的编码智能体（DSH / Claude Code）阅读。

## 项目是什么

一条 AI 级联生成链路：`原著文本 → 文本分片预处理 → 资产拆解 → 导演镜头拆解 → 按镜视频生成`。
支持文本粘贴与 `.txt` 上传，异步任务处理，前端轮询与 SSE 展示进度与结果。

资产拆解早于镜头拆解，输入是原著源文本（`tasks.source_text`），与服务层
`backend/app/services/asset_extractor.py` 共用；任务已有资产时该阶段直接跳过。

分镜阶段按 **导演镜头脚本模板** 输出（片头定调段 + 逐镜块 + 导演阐述），
模板真源见 `backend/app/services/director_storyboard_skill.py` 与 skill `director-storyboard`。

## 技术栈

- 后端：Python FastAPI 0.115 + SQLAlchemy 2.0 + PostgreSQL 18（`backend/`）
- LLM：Anthropic Claude API（注意：应用调用的是 Claude，不是 DSH 的模型）
- 前端：Vue 3.5 + Element Plus 2.9 + Vite 6（`frontend/`）
- 任务队列：`asyncio.Queue` 内存队列（重启即丢失，Demo 模拟）
- 部署：Docker + Compose

## 关键目录

- `backend/app/main.py` — FastAPI 入口
- `backend/app/services/` — 核心逻辑：`text_processor.py`（分片/校验/Token）、`task_queue.py`（内存队列）、`task_manager.py`（级联编排+超时）、`llm_service.py`（Claude API 封装）
- `backend/app/models/` / `schemas/` / `routers/` — ORM / Pydantic / 路由
- `frontend/src/views/` — 4 个页面（上传 / 任务列表 / 结果 / 看板）
- `frontend/src/components/` — 可复用组件

## 常用命令

```bash
# 后端（开发）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 前端（开发）
cd frontend
npm install
npm run dev

# 前端（构建，产物直接写入 backend/static，由 FastAPI 托管）
cd frontend && npm run build

# Docker 一键部署
ANTHROPIC_API_KEY=sk-ant-xxxx docker compose up -d
```

- API 文档：http://127.0.0.1:8000/docs
- 前端：http://localhost:5173
- `vite.config.js` 的 `build.outDir` 指向 `../backend/static` 且 `emptyOutDir: true`，
  构建即产出到托管目录并清掉旧 hash 文件：**不要再手动 `cp -r dist backend/static`**
  （手动复制不清理，会在 `backend/static/assets/` 堆积废弃产物）。
  Dockerfile 里对应的 COPY 源路径是构建阶段的 `/backend/static`，与仓库的 `backend/` 无关。

## 配置与密钥

- 密钥与参数在 `backend/.env`（模板 `backend/.env.example`）；`ANTHROPIC_API_KEY` 必填。
- 数据库为 PostgreSQL（`postgresql+psycopg://aigc:aigc_pass@127.0.0.1:5432/aigc_workbench`），连接串在 `backend/.env` 的 `DATABASE_URL`。
- 无 Alembic：表结构由 `database.py` 的 `create_all` 按 model 建全表，`backend/migrations/` 下是手工执行的一次性脚本（幂等，会跳过已存在的列）。

## 需要遵守的约定

- 级联链路顺序固定：文本分片 → 资产拆解 → 导演镜头拆解（各一次 LLM 调用），
  各有独立超时（单次 600s、阶段按重试次数推算、总 1800s），不要改动顺序。
  **资产拆解两处口径必须一致**：链路（`task_manager._run_pipeline`）与手动接口
  （`POST /tasks/{id}/assets/extract`）都走 `asset_extractor.extract_assets_from_source`。
- 重新生成（`POST /tasks/{id}/regenerate`）**不删资产**：AssetItem 与 `media/{task_id}/{assets,characters}`
  下的图片保留，链路检测到已有资产即跳过资产拆解；重做资产只能靠前端的「AI 重新提取」按钮。
  「AI 重新提取」只覆盖文本字段，`image_path/url/oss_key/portrait_*/image_status` 一律不动，
  新结果未覆盖且未出图的旧资产才删除（见 `asset_extractor._merge_assets`）。
- 输入上限 200000 字符，分片 8000 字符，送入 LLM 最多 3 片；这些常量在 `backend/app/config.py`。
- 错误统一映射为中文可读消息（`backend/app/utils/exceptions.py`）。
- 前端 3s 轮询。不要引入超出 Demo 边界的能力（登录/鉴权、支付、Redis/K8s、多媒体生成等）。

## 技能

- DSH 从 `.agents/skills/` 加载技能；Claude Code 从 `.claude/skills/` 加载。
- 当前技能：`director-storyboard`（导演镜头拆解）、`character-three-view`（角色三视图）、`ui-ux-pro-max`（UI/UX 设计）、caveman 系列（沟通压缩）。
- ⚠️ **模板双份同步**：`director-storyboard` 的模板同时存在于
  `.agents/skills/director-storyboard/SKILL.md`（文档版）与
  `backend/app/services/director_storyboard_skill.py` 的 `DIRECTOR_STORYBOARD_SKILL`（运行时真源）。
  后者才是后端实际调用的 prompt（Dockerfile 只 `COPY backend/` 且 `.dockerignore` 排除 `*.md`，
  容器里读不到 `.agents/`）。**改任一处必须同步另一处**，并同步 `.claude/skills/` 的镜像。
- 解析器 `task_manager._parse_director_storyboard` 依赖模板里固定的结构（`镜头NN：标题（时长：N秒）`、
  `M-N秒：` 分秒行、`摄影与视觉要求：`、`镜头：景别=…｜角度=…｜运镜=…｜情绪=…｜构图=…｜转场=…` 键值行），
  改模板必须同步解析器，否则字段会退化为空。
