/**
 * Vue 应用入口
 * 挂载 Element Plus、Vue Router、全局样式
 */
import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";

import App from "./App.vue";
import router from "./router";
import "./styles/global.scss";

const app = createApp(App);
app.use(ElementPlus);
app.use(router);
app.mount("#app");
