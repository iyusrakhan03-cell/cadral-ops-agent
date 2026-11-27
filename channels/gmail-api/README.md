# Email channel over the Gmail API (OAuth)

The backend reads the shop's Gmail inbox and sends replies itself (`backend/app/gmail.py`). It replaces the
Apps Script bridge in `channels/gmail-bridge/`, which was only needed while the backend ran on a Hugging Face
Space (no Gmail access there). Same inbox/outbox, same retries: an email is marked read only after it's saved,
and a reply is confirmed only after Gmail sent it. Replies go into the customer's own thread.

## Setup (once, ~10 minutes)

1. **Google Cloud project:** <https://console.cloud.google.com> → create a project ("Thistle & Pine").
2. **Turn on the Gmail API:** APIs & Services → Library → "Gmail API" → Enable.
3. **Consent screen:** APIs & Services → OAuth consent screen (Google Auth Platform) → External → app name
   "Thistle & Pine", support email = the shop Gmail. Under **Audience**, add the shop Gmail as a test user.
   Then click **Publish app** (status "In production"). No verification submission is needed for your own account;
   it just shows an "unverified app" warning at sign-in. **Why publish:** in "Testing" status Google expires the
   login every 7 days, and email would silently stop.
4. **Client:** Credentials → Create credentials → OAuth client ID → type **Desktop app**. No redirect URL needed.
   (A "Web application" client also works if you add `http://localhost:8765/oauth/callback`.)
5. Put the two values in the repo's `.env` on the machine that runs the backend:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```
6. **Sign in once** (same machine):
   ```
   cd backend
   .venv\Scripts\python -m scripts.gmail_auth      # Windows
   .venv/bin/python -m scripts.gmail_auth          # Mac/Linux
   ```
   A browser opens: sign in as the **shop** Gmail → *Advanced → Go to Thistle & Pine (unsafe)* → Allow.
   It saves `GMAIL_REFRESH_TOKEN` to `.env` and prints the connected address.
7. Restart the backend (`deploy\run_live.ps1`). Dashboard → Settings shows **Email (Gmail API · address) ✓ connected**.

If the Apps Script trigger is still running, delete it (script.google.com → Triggers). Running both is harmless
(each email is leased to one sender) but unnecessary.

**Test:** from another account, email the shop address *"Do you deliver to Northern Ireland?"*. The reply arrives
in the same thread within ~30 seconds and the chat shows in the dashboard with channel `email`.

**If the login stops working** (password change, access revoked): Settings shows the error and Slack gets an
alert. Run step 6 again and restart. Scope used: `gmail.modify` (read, mark read, send; never deletes).
