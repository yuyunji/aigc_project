/**
 * Vue Router 路由配置
 * 四个主要页面：上传、任务列表、结果预览、统计看板
 */
import { createRouter, createWebHistory } from "vue-router";

const routes = [
  {
    path: "/",
    name: "Upload",
    component: () => import("../views/UploadView.vue"),
    meta: { title: "上传任务" },
  },
  {
    path: "/tasks",
    name: "Tasks",
    component: () => import("../views/TaskListView.vue"),
    meta: { title: "任务管理" },
  },
  {
    path: "/results",
    name: "Results",
    component: () => import("../views/ResultView.vue"),
    meta: { title: "结果预览" },
  },
  {
    path: "/dashboard",
    name: "Dashboard",
    component: () => import("../views/DashboardView.vue"),
    meta: { title: "统计看板" },
  },
  {
    path: "/media",
    name: "Media",
    component: () => import("../views/MediaView.vue"),
    meta: { title: "媒体预览" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
