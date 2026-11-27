"""One-time Google sign-in for the email channel: saves GMAIL_REFRESH_TOKEN to the repo's .env.

    cd backend
    python -m scripts.gmail_auth            (Windows: .venv\\Scripts\\python -m scripts.gmail_auth)

Before running: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env (Google Cloud Console -> APIs & Services ->
Credentials -> OAuth client ID, type "Desktop app"; a "Web application" client works too if it lists the redirect
URI http://localhost:8765/oauth/callback).

A browser opens Google's consent screen. Sign in as the SHOP's Gmail account and allow access.
The token is written straight into .env and never printed. Restart the backend afterwards.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

from app.config import REPO_DIR, get_settings
from app.gmail import SCOPE, TOKEN_URL

PORT = 8765
REDIRECT = f"http://localhost:{PORT}/oauth/callback"
ENV_FILE = REPO_DIR / ".env"


def save_env(key: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    st = get_settings()
    if not (st.google_client_id and st.google_client_secret):
        print(f"Put GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in {ENV_FILE} first.")
        return 1

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            url = urllib.parse.urlparse(self.path)
            if url.path != "/oauth/callback":
                self.send_response(404); self.end_headers(); return
            q = dict(urllib.parse.parse_qsl(url.query))
            ok = q.get("state") == state and "code" in q
            result.update(q if ok else {"error": q.get("error", "state mismatch")})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab and go back to the terminal." if ok \
                else f"Sign-in failed: {result['error']}. Close this tab and try again."
            self.wfile.write(f"<html><body style='font-family:sans-serif;padding:40px'><h2>{msg}</h2></body></html>".encode())
            done.set()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": st.google_client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google sign-in in your browser. Sign in as the SHOP's Gmail account.")
    print("If nothing opens, paste this link into a browser:\n\n" + auth_url + "\n")
    webbrowser.open(auth_url)

    if not done.wait(timeout=600):
        print("Timed out after 10 minutes. Run it again.")
        return 1
    server.shutdown()
    if "error" in result:
        print(f"Sign-in failed: {result['error']}")
        return 1

    r = httpx.post(TOKEN_URL, timeout=30, data={
        "client_id": st.google_client_id, "client_secret": st.google_client_secret, "code": result["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": REDIRECT})
    if r.status_code != 200:
        print(f"Google refused the code: {r.status_code} {r.text[:300]}")
        return 1
    tok = r.json()
    if "refresh_token" not in tok:
        print("Google returned no refresh token. Remove the app's access at https://myaccount.google.com/permissions "
              "and run this again.")
        return 1
    me = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout=20,
                   headers={"Authorization": f"Bearer {tok['access_token']}"}).json().get("emailAddress", "?")
    save_env("GMAIL_REFRESH_TOKEN", tok["refresh_token"])
    print(f"Done. Connected to {me}. Token saved to {ENV_FILE}.")
    print("Restart the backend; the email channel starts automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
