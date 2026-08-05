<!--
  根组件 —— Glassmorphism 导航 + 路由出口
-->
<template>
  <div class="app-container">
    <header class="app-header">
      <div class="header-left">
        <span class="header-brand">🎬</span>
        <h1 class="app-title">AIGC 短剧工作台</h1>
        <el-tag size="small" type="info" effect="dark" class="demo-badge" round>Demo</el-tag>
      </div>
      <nav class="app-nav">
        <router-link to="/" exact-active-class="nav-active">
          <span class="nav-icon">📝</span>上传
        </router-link>
        <router-link to="/tasks" active-class="nav-active">
          <span class="nav-icon">📋</span>任务
        </router-link>
        <router-link to="/results" active-class="nav-active">
          <span class="nav-icon">📖</span>结果
        </router-link>
        <router-link to="/dashboard" active-class="nav-active">
          <span class="nav-icon">📊</span>看板
        </router-link>
      </nav>
    </header>

    <main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <footer class="app-footer">
      <span>AIGC Short Drama Workbench · Personal Demo · Built with Vue 3 + FastAPI + Claude</span>
    </footer>
  </div>
</template>

<script setup>
// 纯布局，无业务逻辑
</script>

<style lang="scss" scoped>
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.header-brand {
  font-size: 24px;
  line-height: 1;
  filter: drop-shadow(0 0 8px rgba(99, 102, 241, 0.4));
}

.app-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text-inverse);
  letter-spacing: 0.3px;
}

.demo-badge {
  font-size: 10px;
  letter-spacing: 0.5px;
  opacity: 0.8;
}

.app-nav {
  display: flex;
  align-items: center;
  gap: 4px;

  a {
    color: rgba(255, 255, 255, 0.6);
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    padding: 7px 14px;
    border-radius: var(--radius-sm);
    transition: all var(--transition-fast);
    display: flex;
    align-items: center;
    gap: 5px;

    .nav-icon {
      font-size: 14px;
      line-height: 1;
    }

    &:hover {
      color: var(--color-text-inverse);
      background: rgba(255, 255, 255, 0.08);
    }

    &.nav-active {
      color: var(--color-text-inverse);
      background: rgba(99, 102, 241, 0.3);
      box-shadow: 0 0 12px rgba(99, 102, 241, 0.2);
    }
  }
}

.app-footer {
  text-align: center;
  padding: 14px var(--space-lg);
  font-size: 12px;
  color: var(--color-text-tertiary);
  flex-shrink: 0;
  border-top: 1px solid var(--color-border-light);
  background: var(--color-surface);
  letter-spacing: 0.2px;
}

// 页面过渡动画
.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity var(--transition-base), transform var(--transition-base);
}
.page-fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.page-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 768px) {
  .app-nav a {
    padding: 6px 10px;
    font-size: 12px;
    gap: 3px;

    .nav-icon { font-size: 13px; }
  }
}
</style>
