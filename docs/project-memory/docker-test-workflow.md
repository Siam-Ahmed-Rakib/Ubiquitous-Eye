---
name: docker-test-workflow
description: "How to run the backend pytest suite in Docker, and the -w /app path gotcha"
metadata: 
  node_type: memory
  type: project
  originSessionId: 241d6d28-6325-4f26-9185-9cf014e2bdd1
---

Backend source is baked into the image; `server/tests/` is NOT copied by the Dockerfile. After a code change, rebuild and run tests like this:

1. `docker compose build backend && docker compose up -d backend`
2. `docker exec capstone-backend pip install -q pytest` (pytest isn't in requirements; reinstall after each rebuild)
3. `docker cp E:/Ubiquitous-Eye/server/tests capstone-backend:/app/tests`
4. `docker exec -w /app capstone-backend python -m pytest tests/ -q -p no:warnings`

**Gotcha:** run step 4 (any `docker exec -w /app ...`) via the **PowerShell tool, NOT the Bash tool**. Git Bash (MSYS) rewrites the `/app` argument into a Windows path, so `docker exec -w /app` fails with `OCI runtime exec failed: ... Cwd must be an absolute path`. PowerShell passes `/app` through unchanged. (If you must use Bash, prefix `MSYS_NO_PATHCONV=1`.)

Backend is on host port **5001** (`docker-compose.override.yml` remaps 5000→5001). Docker Desktop is often down at session start — launch `"C:\Program Files\Docker\Docker\Docker Desktop.exe"` and poll `docker info` until ready.

Related: [[result-cache-architecture]]
