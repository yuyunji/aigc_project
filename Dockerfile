# ================================================================
# AIGC 短剧工作台 — Docker 镜像
# 多阶段构建：前端 Node 构建 + 后端 Python 运行
# 用途：本地演示、面试展示，非生产部署
# ================================================================

# ─────────────────── 阶段 1: 前端构建 ───────────────────
FROM node:20-alpine AS frontend-build

# 使用国内 npm 镜像加速
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent 2>/dev/null || npm install --silent

COPY frontend/ ./
RUN npm run build

# ─────────────────── 阶段 2: 后端运行镜像 ───────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="AIGC Short Drama Workbench"
LABEL org.opencontainers.image.description="Personal demo project — AI-assisted script generation"
LABEL org.opencontainers.image.version="0.1.0"

WORKDIR /app

# 安装系统依赖（build-essential 用于编译 oss2 的依赖 crcmod，其为纯源码包）
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends curl build-essential && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（阿里云 pip 镜像加速）
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# 复制后端代码
COPY backend/ .

# 复制前端构建产物到后端静态目录
COPY --from=frontend-build /frontend/dist ./static/

# 创建数据 & 上传目录
RUN mkdir -p uploads

# 环境变量默认值（运行时通过 -e 或 .env 覆盖）
ENV HOST=0.0.0.0
ENV PORT=8000
ENV ANTHROPIC_API_KEY=""
ENV DATABASE_URL=mysql+pymysql://aigc:aigc_pass@mysql:3306/aigc_workbench?charset=utf8mb4

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["sh", "-c", "mkdir -p data && uvicorn app.main:app --host ${HOST} --port ${PORT}"]
