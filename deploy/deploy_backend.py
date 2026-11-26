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
           "AGENT_EMAIL", "AGENT_PASSWORD", "CORS_ORIGINS"]

env = {k: v for k, v in dotenv_values(REPO / ".env").items() if v}
token = env.get("HF_TOKEN")
if not token:
    sys.exit("Add HF_TOKEN=... (a Hugging Face token with write access) to .env first.")

api = HfApi(token=token)
user = api.whoami()["name"]
space = f"{user}/{SPACE_NAME}"
api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)

for key in SECRETS:
    if env.get(key):
        api.add_space_secret(space, key, env[key])
        print(f"secret set: {key}")
missing = [k for k in ("GROQ_API_KEY", "JWT_SECRET", "ADMIN_PASSWORD", "AGENT_PASSWORD", "CORS_ORIGINS") if not env.get(k)]
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
