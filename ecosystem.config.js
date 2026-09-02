/**
 * PM2 ecosystem — NWP Verification Dashboard (webpsi)
 *
 * Start:  pm2 start ecosystem.config.js
 * Logs:   pm2 logs nwp-verify-api
 * Restart: pm2 restart all
 */
module.exports = {
  apps: [
    {
      name: "nwp-verify-api",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "-m uvicorn backend.main:app --host 127.0.0.1 --port 8013",
      env: {
        PYTHONPATH: ".",
        API_PORT: "8013",
      },
      instances: 1,
      autorestart: true,
      max_memory_restart: "4G",
      error_file: "logs/api-error.log",
      out_file: "logs/api-out.log",
      merge_logs: true,
      time: true,
    },
    {
      name: "nwp-verify-frontend",
      cwd: __dirname,
      script: ".venv/bin/python",
      args: "serve_frontend.py",
      env: {
        FRONTEND_PORT: "3013",
      },
      instances: 1,
      autorestart: true,
      max_memory_restart: "512M",
      error_file: "logs/frontend-error.log",
      out_file: "logs/frontend-out.log",
      merge_logs: true,
      time: true,
    },
  ],
};
