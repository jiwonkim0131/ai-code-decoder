import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 로컬 개발: uvicorn(8000)을 따로 띄우고 /api는 프록시로 보낸다. 배포에서는 FastAPI가 dist/를 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
