/**
 * PM2 ecosystem — MONAS @ psimkg.bmkg.go.id/monas (webpsi)
 *
 * Mode produksi: SERVE_READONLY — hanya baca artifact dari PC/HPC.
 * Deploy path: /var/www/monas
 * Start:  pm2 startOrReload ecosystem.config.cjs
 * Setelah edit .env: pm2 startOrReload ecosystem.config.cjs && pm2 restart monas-api --update-env
 */
const fs = require("fs");
const path = require("path");

function readDotEnv(file) {
  const out = {};
  try {
    const text = fs.readFileSync(path.resolve(__dirname, file), "utf8");
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) continue;
      const i = line.indexOf("=");
      const k = line.slice(0, i).trim();
      const v = line.slice(i + 1).trim().replace(/^['"]|['"]$/g, "");
      if (k) out[k] = v;
    }
  } catch (_) { /* .env opsional */ }
  return out;
}

const fileEnv = readDotEnv(".env");

module.exports = {
  apps: [
    {
      name: "monas-api",
      cwd: "/var/www/monas",
      script: ".venv/bin/python",
      args: "-m uvicorn backend.main:app --host 127.0.0.1 --port 8013",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "1G",
      error_file: "logs/api-error.log",
      out_file: "logs/api-out.log",
      merge_logs: true,
      time: true,
      env_file: ".env",
      env: {
        NODE_ENV: "production",
        PYTHONPATH: ".",
        API_HOST: "127.0.0.1",
        API_PORT: "8013",
        BASE_PATH: "/monas",
        CORS_ORIGIN: "https://psimkg.bmkg.go.id",
        SEED_DEMO_DATA: "false",
        // Serve-only: hitungan di PC/HPC, sync artifact harian
        SERVE_READONLY: "true",
        ENABLE_PIPELINE_SCHEDULER: "false",
        FORCE_PIPELINE: "false",
        AUTO_SYNC_OBS: "false",
        DISABLE_STARTUP_PIPELINE: "true",
        DATA_DIR: "./data",
        CARTO_API_KEY: fileEnv.CARTO_API_KEY || process.env.CARTO_API_KEY || "",
      },
    },
    {
      name: "monas-web",
      cwd: "/var/www/monas",
      script: "server-static.js",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "512M",
      error_file: "logs/web-error.log",
      out_file: "logs/web-out.log",
      merge_logs: true,
      time: true,
      env: {
        NODE_ENV: "production",
        HOSTNAME: "127.0.0.1",
        PORT: "3013",
        BASE_PATH: "/monas",
      },
    },
  ],
};
