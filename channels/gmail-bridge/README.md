# Gmail bridge — the email channel

Customers email the shop's Gmail address → the AI answers in the same email thread. The daily management
report is also sent from this Gmail.

`Code.gs` is a free Google Apps Script that runs **inside the shop's Gmail account** every minute:

1. Finds unread emails in the inbox and posts them to the backend (`/api/channels/email/inbound`).
   An email is only marked read **after** the backend confirms it saved it.
2. Collects replies waiting in the backend's outbox (`/api/channels/email/outbox`), sends them from Gmail
   (as a reply in the customer's thread), then confirms each one. Failures are retried automatically.

It also keeps the free Hugging Face Space awake, because it calls the API every minute.

**Why not have the server read Gmail itself?** Free Hugging Face Spaces only allow web (HTTPS) traffic, and
Gmail's IMAP/SMTP ports are blocked there. Apps Script runs on Google's side, so it can use Gmail directly and
reach our API over HTTPS.

## Setup (about 5 minutes)

Use a **separate Gmail account for the shop** (e.g. a new `thistlepine.support…@gmail.com`). The bridge
answers every unread email in that inbox, so don't point it at a personal inbox.

1. Signed in as the shop Gmail, open <https://script.google.com> → **New project**. Name it "Thistle & Pine bridge".
2. Delete the sample code, paste the whole of `Code.gs`, and click **Save**.
3. **Project Settings** (gear icon) → **Script properties** → add two properties:
   - `API_URL`: the backend URL, e.g. `https://<hf-user>-thistle-pine-api.hf.space`
   - `BRIDGE_KEY`: the same `BRIDGE_KEY` that's in the backend's `.env` / Space secrets
4. Back in the editor, choose the function **`setup`** in the toolbar and click **Run**.
   Google asks for permission: **Review permissions** → pick the shop account → **Advanced** →
   **Go to Thistle & Pine bridge (unsafe)** → **Allow**. (It says "unsafe" only because you wrote the script
   yourself and Google hasn't reviewed it.)
5. The log should say *Connected to the backend and scheduled every minute*. Done.

**Test:** from any other email account, email the shop address, for example *"Do you deliver to Northern
Ireland?"*. Within 1–2 minutes the reply arrives in the same thread, and the conversation shows in the
dashboard with channel `email`.

To verify as a demo customer by email, write something like *"I'm emma.clarke@example.com, order TP-100482.
Can I get a refund? It arrived damaged."*

**Limits (free Gmail):** about 100 emails sent per day, and scripts can run for 90 minutes per day in total.
That's plenty for a demo. Each one-minute run takes about 1–2 seconds when there's nothing to do.

**To pause:** in the Apps Script editor, open **Triggers** (clock icon) and delete the `run` trigger.
