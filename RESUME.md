# AI 全栈项目经验 —— AIGC 短剧工作台

> 适用于简历的项目描述，按篇幅需求选择对应版本。

---

## 标准版（推荐，适合项目经历栏）

### AIGC 短剧工作台 —— 个人全栈项目

- 独立设计并实现基于 **FastAPI + Vue 3** 的 AI 短剧生成平台，完成 **9 个后端服务模块**、**5 个前端页面**、**10 个可复用组件**的全流程开发
- 设计 **8 阶段 AI 级联流水线架构**（文本→大纲→角色→分镜→图片→视频→配音→合成），实现异步任务编排、阶段级超时保护、指数退避重试、异常分类映射等生产级容错机制
- 集成 **4 个 AI API**：Claude API（文本生成，Sonnet 5）、DashScope Wan-X-Turbo（分镜图片生成）、fal.ai Seedance 1.5 Pro（图生视频）、火山引擎 TTS（角色配音），统一封装重试/超时/Token 预算管理
- 实现 **智能 Token 管理**：中英文混合 Token 估算算法、分阶段自适应截断策略（根据上下文窗口动态分配各阶段输入预算）、API 精确校验兜底
- 前端采用 **Vue 3 Composition API + Element Plus**，自建 SCSS 设计 Token 系统（50+ 变量），实现 Glassmorphism 导航、Bento Grid 响应式布局、进度轮询、自研 Markdown 渲染器、CSS 柱状图统计看板
- 使用 **Docker 多阶段构建**（Node 构建 + Python 运行镜像），配合 Docker Compose 一键启动，含健康检查、数据持久化、环境变量配置分层
- 实现 **FFmpeg 视频合成**：多段视频拼接 + 音频混音 + SRT 字幕烧录，完成端到端"文本→成片"闭环
- 独立完成全部 **Prompt Engineering**：4 套结构化 System Prompt（大纲/角色/分镜/图提示词翻译），含 JSON Schema 约束、Markdown 兜底解析、角色扮演指令

---

## 精简版（适合篇幅紧张）

> **AIGC 短剧工作台** | FastAPI + Vue 3 + Claude API | 个人项目
> - 设计 8 阶段 AI 级联流水线，集成 Claude / Wan-X / Seedance / TTS 四个 AI API，实现文本→分镜→视频的全自动生成链路
> - 自研 Token 预算管理 & 自适应截断策略，封装统一重试/超时/异常映射机制
> - 前端自建设计系统（50+ Token、Glassmorphism、Bento Grid），Docker 多阶段构建一键部署

---

## 面试展开版（技术深挖参考）

### 项目定位
短剧剧本 AI 辅助生成平台，Demo 项目，无鉴权/多用户/持久化队列。

### 技术栈
| 层级 | 技术 |
|------|------|
| 后端框架 | Python FastAPI 0.115（async） |
| 数据库 | SQLite + SQLAlchemy 2.0 ORM |
| AI 文本 | Anthropic Claude API（Sonnet 5） |
| AI 图片 | DashScope Wan-X-Turbo |
| AI 视频 | fal.ai Seedance 1.5 Pro |
| AI 语音 | 火山引擎 TTS |
| 前端框架 | Vue 3.5 Composition API |
| UI 组件 | Element Plus 2.9 |
| 样式 | SCSS + 自定义 Design Token 系统 |
| 构建 | Vite 6.0 |
| 部署 | Docker 多阶段构建 + Docker Compose |

### 架构亮点

#### 1. 8 阶段流水线编排
```
输入校验 → 文本分片 → 大纲生成 → 角色生成 → 分镜生成
         → 图片生成(Wan-X) → 视频生成(Seedance) → 配音(TTS) → 合成(FFmpeg)
```
- 阶段 1-4 为文本链路，阶段 5-8 为媒体链路（可配置关闭）
- 每阶段更新进度（5%→100%），前端 3s 轮询
- 单 worker 消费 asyncio.Queue，任务串行执行

#### 2. 三级超时 + 重试体系
- 单次 API 调用超时：120s
- 阶段级超时：130s（API 超时 + 10s 余量）
- 任务总超时：600s
- 指数退避重试：2s → 4s，最多 2 次
- 5xx 重试，4xx 不重试（Rate Limit 除外）
- Token 超限不重试（不可恢复错误）

#### 3. Token 预算管理
- 估算公式：中文 1.5 字符/token，英文 4 字符/token
- 预估算 → 软警告（不阻断）→ API 精确校验兜底
- 分阶段智能截断：大纲阶段取前 3 片，角色阶段动态计算剩余预算，分镜阶段再压缩大纲

#### 4. 结构化输出 + 兜底
- 分镜生成要求 Claude 输出严格 JSON 数组
- 主解析器：JSON 提取 + code block 清理 + 类型转换
- 兜底解析器：Markdown 正则匹配（JSON 解析失败时自动切换）
- 图片 Prompt 生成同样有翻译失败时的中文兜底

#### 5. 前端设计系统
- 50+ CSS 自定义属性（品牌色、语义色、中性色、阴影、圆角、间距、字体）
- AI Purple (#6366F1) 主题色 + 深色侧边栏
- Bento Grid 卡片布局（CSS Grid + span 控制）
- Glassmorphism 导航栏（backdrop-filter 毛玻璃）
- 自研 Markdown → HTML 渲染器（不依赖第三方库）

#### 6. 边界保护
- 输入校验：空文本、超短文本（<10字）、超长文本（>200,000字）
- 文件上传：编码检测链（UTF-8 → GBK → Latin-1）
- 错误友好化：所有异常映射为中文可读消息
- 每个媒体阶段独立失败，不影响后续阶段

### 可展开的面试话题
- "为什么用 asyncio.Queue 而不是 Celery/Redis？" → Demo 范围取舍，内存队列足够单用户单 worker，零依赖降低部署复杂度
- "为什么 SQLAlchemy 同步引擎 + asyncio.to_thread？" → SQLite 不支持 asyncpg，to_thread 是务实方案
- "JSON 输出稳定性如何保证？" → System Prompt 强制约束 + 主备双解析器 + 角色名正则过滤防混入
- "媒体链路失败怎么处理？" → 每阶段 try-catch 独立，MediaAsset 表记录每条资产的成功/失败状态，前端展示失败原因
