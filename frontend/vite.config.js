import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  build: {
    // 直接产出到 FastAPI 托管的静态目录，省去「构建后手动 cp 到 backend/static」这一步。
    // 手动复制不清理旧 hash 文件，会在 backend/static/assets 里越堆越多。
    // outDir 落在项目根之外，Vite 默认拒绝清空它，必须显式 emptyOutDir。
    outDir: "../backend/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/media": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
