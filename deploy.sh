#!/bin/bash
set -e

cd ~/aigc_project

echo ">>> 拉取最新代码..."
git pull origin master

echo ">>> 同步 .env 配置..."
# 把 backend/.env 复制到根目录供 docker-compose 读取
cp backend/.env .env

echo ">>> 重新构建并重启..."
docker compose up -d --build

echo ">>> 清理旧镜像..."
docker image prune -f

echo ">>> 完成！"
docker compose ps
