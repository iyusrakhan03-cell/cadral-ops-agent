"""Deploy the backend to a Hugging Face Space (Docker) and copy secrets from .env into the Space.

    backend/.venv/bin/python deploy/deploy_backend.py            # uses HF_TOKEN from .env

Creates <hf-user>/thistle-pine-api if needed, uploads backend/ + data/docs + the Space files, and sets the
secrets below as Space secrets (never uploaded as files). Prints the public API URL at the end.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

REPO = Path(__file__).resolve().parent.parent
SPACE_NAME = "thistle-pine-api"
SECRETS = ["GROQ_API_KEY", "PINECONE_API_KEY", "DATABASE_URL", "JWT_SECRET", "ADMIN_EMAIL", "ADMIN_PASSWORD",
           "AGENT_EMAIL", "AGENT_PASSWORD", "CORS_ORIGINS",
           # Day 4: channels, alerts, daily report, database backup
           "HF_TOKEN", "DB_BACKUP_REPO", "BRIDGE_KEY", "RELAY_URL", "SLACK_WEBHOOK_URL", "MANAGER_EMAIL",
           "DASHBOARD_URL", "REPORT_HOUR", "REPORT_TIMEZONE"]
BACKUP_DATASET = "thistle-pine-data"

ENV_FILE = REPO / ".env"
env = {k: v for k, v in dotenv_values(ENV_FILE).items() if v}
token = env.get("HF_TOKEN")
if not token:
    sys.exit("Add HF_TOKEN=... (a Hugging Face token with write access) to .env first.")

api = HfApi(token=token)
user = api.whoami()["name"]
space = f"{user}/{SPACE_NAME}"

# Fill in the values that can be generated, and remember them in .env (never committed).
import secrets as _secrets
generated = {}
for key in ("BRIDGE_KEY", "JWT_SECRET"):
    if not env.get(key):
        generated[key] = _secrets.token_urlsafe(32)
if not env.get("DB_BACKUP_REPO"):
    generated["DB_BACKUP_REPO"] = f"{user}/{BACKUP_DATASET}"
if env.get("DASHBOARD_URL") and not env.get("RELAY_URL"):
    generated["RELAY_URL"] = env["DASHBOARD_URL"]
if env.get("DASHBOARD_URL") and not env.get("CORS_ORIGINS"):
    generated["CORS_ORIGINS"] = f"{env['DASHBOARD_URL'].rstrip('/')},http://localhost:3000"
if generated:
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write("\n# --- added by deploy/deploy_backend.py ---\n")
        for k, v in generated.items():
            f.write(f"{k}={v}\n")
            print(f"generated {k} (saved to .env)")
    env.update(generated)

api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
api.create_repo(env["DB_BACKUP_REPO"], repo_type="dataset", private=True, exist_ok=True)   # database backups

for key in SECRETS:
    if env.get(key):
        api.add_space_secret(space, key, env[key])
        print(f"secret set: {key}")
missing = [k for k in ("GROQ_API_KEY", "ADMIN_PASSWORD", "AGENT_PASSWORD", "CORS_ORIGINS", "DASHBOARD_URL",
                       "SLACK_WEBHOOK_URL", "MANAGER_EMAIL") if not env.get(k)]
if missing:
    print("warning: not set in .env:", ", ".join(missing))

api.upload_folder(repo_id=space, repo_type="space", folder_path=REPO / "deploy" / "hf-space",
                  commit_message="Space config")
api.upload_folder(repo_id=space, repo_type="space", folder_path=REPO / "backend", path_in_repo="backend",
                  ignore_patterns=[".venv/**", "**/__pycache__/**", "models/**", ".fastembed_cache/**", "data/eval.db",
                                   "*.log", ".env", "data/local.db"], commit_message="Backend code")
# Ship the demo database as committed in git, not the local working copy (which holds staff password hashes
# and test chats). The Space creates its staff accounts from the secrets above on first start.
import subprocess
committed_db = subprocess.run(["git", "show", "HEAD:backend/data/local.db"], cwd=REPO, capture_output=True, check=True).stdout
api.upload_file(path_or_fileobj=committed_db, path_in_repo="backend/data/local.db", repo_id=space, repo_type="space",
                commit_message="Demo database")
api.upload_folder(repo_id=space, repo_type="space", folder_path=REPO / "data" / "docs", path_in_repo="data/docs",
                  commit_message="Knowledge base")

host = f"https://{user}-{SPACE_NAME}.hf.space".lower().replace("_", "-")
print(f"\nSpace: https://huggingface.co/spaces/{space}\nAPI:   {host}  (first build takes ~5-10 min)")
print(f"""
Next:
  1. Vercel > your dashboard project > Settings > Environment Variables, then redeploy:
       NEXT_PUBLIC_API_URL = {host}
       BRIDGE_KEY          = (the BRIDGE_KEY line in your .env)
       TELEGRAM_BOT_TOKEN / TELEGRAM_WEBHOOK_SECRET   (see deploy/set_telegram_webhook.py)
  2. Gmail bridge: channels/gmail-bridge/README.md   (API_URL = {host})
  3. Health check: {host}/api/health
""")
