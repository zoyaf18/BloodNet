import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
const backend = process.env.BLOODNET_DEV_BACKEND_URL || "http://127.0.0.1:8080";

export default defineConfig({
	plugins: [react()],
	server: {
		proxy: {
			"/match-svc": backend,
			"/swarm-svc": backend,
			"/intake-svc": backend,
			"/graph-svc": backend,
			"/copilot-svc": backend,
			"/agent-svc": backend,
			"/api/v1": backend,
		},
	},
});
