/**
 * PM2 ecosystem — Verifikasi InaNWP @ psimkg.bmkg.go.id/verifikasi-inanwp
 *
 * Deploy path: /var/www/verifikasi-inanwp
 * Start:  pm2 startOrReload ecosystem.config.cjs
 * Logs:   pm2 logs verifikasi-inanwp-api
 */
module.exports = {
  apps: [
    {
      name: "verifikasi-inanwp-api",
      cwd: "/var/www/verifikasi-inanwp",
      script: ".venv/bin/python",
      args: "-m uvicorn backend.main:app --host 127.0.0.1 --port 8013",
      instances: 1,
      exec_mode: "fork",
      autorestart: true,
      max_memory_restart: "4G",
      error_file: "logs/api-error.log",
      out_file: "logs/api-out.log",
      merge_logs: true,
      time: true,
      env: {
        NODE_ENV: "production",
        PYTHONPATH: ".",
        API_HOST: "127.0.0.1",
        API_PORT: "8013",
        BASE_PATH: "/verifikasi-inanwp",
        CORS_ORIGIN: "https://psimkg.bmkg.go.id",
        SEED_DEMO_DATA: "false",
        FORCE_PIPELINE: "true",
        AUTO_SYNC_OBS: "true",
        DATA_DIR: "./data",
      },
    },
    {
      name: "verifikasi-inanwp-web",
      cwd: "/var/www/verifikasi-inanwp",
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
        BASE_PATH: "/verifikasi-inanwp",
      },
    },
  ],
};
