#!/bin/bash
set -e

# ================================================================
# 本地一键部署：推送代码 + 同步 .env + 服务器重建
# 用法:
#   export DEPLOY_SERVER="root@你的服务器IP"
#   ./deploy-to-server.sh
#
# 或写入 ~/.bashrc:
#   echo 'export DEPLOY_SERVER="root@1.2.3.4"' >> ~/.bashrc
# ================================================================

if [ -z "$DEPLOY_SERVER" ]; then
    echo "错误: 请先设置环境变量 DEPLOY_SERVER"
    echo "  export DEPLOY_SERVER=\"root@你的服务器IP\""
    exit 1
fi

PROJECT_DIR="~/aigc_project"

echo ">>> 1/4 推送代码到 GitHub..."
git push origin master

echo ">>> 2/4 同步 .env 到服务器..."
scp backend/.env ${DEPLOY_SERVER}:${PROJECT_DIR}/backend/.env

echo ">>> 3/4 服务器拉代码 + 构建..."
ssh ${DEPLOY_SERVER} "cd ${PROJECT_DIR} && git pull origin master && cp backend/.env .env && docker compose up -d --build && docker image prune -f"

echo ">>> 4/4 完成！"
ssh ${DEPLOY_SERVER} "cd ${PROJECT_DIR} && docker compose ps"
