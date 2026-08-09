#!/bin/bash
# AIGC短剧工作台 — 一键启动前后端
# 用法: bash start.sh

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

cleanup() {
    echo ""
    echo "正在关闭服务..."
    kill $BACKEND_PID 2>/dev/null
    kill $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID 2>/dev/null
    wait $FRONTEND_PID 2>/dev/null
    echo "服务已关闭"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── 启动后端 ──
echo "[1/2] 启动后端 FastAPI (port 8000)..."
cd "$BACKEND_DIR"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

# ── 启动前端 ──
echo "[2/2] 启动前端 Vite (port 5173)..."
cd "$FRONTEND_DIR"
npx vite --host &
FRONTEND_PID=$!

# ── 等待就绪 ──
sleep 3

# 验证后端
if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
    echo "✓ 后端: http://127.0.0.1:8000 (health: ok)"
else
    echo "✗ 后端启动失败，请检查 backend 目录"
fi

# 验证前端
if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5173 2>&1 | grep -q 200; then
    echo "✓ 前端: http://127.0.0.1:5173"
else
    echo "✗ 前端启动失败，请检查 frontend 目录"
fi

echo ""
echo "按 Ctrl+C 关闭所有服务"
wait
