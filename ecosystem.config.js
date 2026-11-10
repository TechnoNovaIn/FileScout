// PM2 ecosystem config for FileScout
// Single-process deployment: FastAPI serves both API + React build on port 3001
// Apply with: pm2 startOrReload ecosystem.config.js
module.exports = {
  apps: [
    {
      name: "filescout",
      cwd: "/home/admins/Filescout/python",
      script: "/home/admins/Filescout/python/.venv/bin/python",
      args: "-m uvicorn search_api:app --host 0.0.0.0 --port 3001",
      exec_mode: "fork",
      instances: 1,
      autorestart: true,
      max_restarts: 20,
      restart_delay: 3000,
      error_file: "/home/admins/logs/filescout.err.log",
      out_file: "/home/admins/logs/filescout.out.log",
      merge_logs: true,
      time: true,
    },
  ],
};
