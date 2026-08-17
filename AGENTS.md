# AIGC 短剧工作台 — 项目指南

> 个人 Demo：AI 辅助短剧剧本生成工作流。供本仓库内的编码智能体（DSH / Claude Code）阅读。

## 项目是什么

一条 AI 级联生成链路：`原著文本 → 文本分片预处理 → 剧本大纲 → 人物角色设定 → 分镜脚本`。
支持文本粘贴与 `.txt` 上传，异步任务处理，前端轮询展示进度与结果。

## 技术栈

- 后端：Python FastAPI 0.115 + SQLAlchemy 2.0 + MySQL（`backend/`）
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

# Docker 一键部署
ANTHROPIC_API_KEY=sk-ant-xxxx docker compose up -d
```

- API 文档：http://127.0.0.1:8000/docs
- 前端：http://localhost:5173

## 配置与密钥

- 密钥与参数在 `backend/.env`（模板 `backend/.env.example`）；`ANTHROPIC_API_KEY` 必填。
- 数据库为 MySQL（`mysql+pymysql://aigc:aigc_pass@127.0.0.1:3306/aigc_workbench`），连接串在 `backend/.env` 的 `DATABASE_URL`。

## 需要遵守的约定

- 级联链路顺序固定：大纲 → 人物 → 分镜，各有独立超时（单次 120s、阶段 130s、总 600s），不要改动顺序。
- 输入上限 200000 字符，分片 8000 字符，送入 LLM 最多 3 片；这些常量在 `backend/app/config.py`。
- 错误统一映射为中文可读消息（`backend/app/utils/exceptions.py`）。
- 前端 3s 轮询。不要引入超出 Demo 边界的能力（登录/鉴权、支付、Redis/K8s、多媒体生成等）。

## 技能

- DSH 从 `.agents/skills/` 加载技能；Claude Code 从 `.claude/skills/` 加载。
- 当前技能：`character-three-view`（角色三视图）、`ui-ux-pro-max`（UI/UX 设计）、caveman 系列（沟通压缩）。
