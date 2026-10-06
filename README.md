# Ops Monitor: Performance Engineering

A self-hosted team web app for airline performance engineers. It runs on one office PC and the team opens it in a browser over the office LAN or VPN. No cloud services, no database server: Python (FastAPI) + SQLite + plain HTML/JS.

- **NOTAM Monitor** (ready, first draft): pulls NOTAMs automatically and shows them in one live sheet with action tracking.
- **Database Monitor** (next phase): AIP vs Jeppesen vs internal database comparison.

## Run it (no Docker needed)
1. Install **Python 3.11+** from python.org ("Install for current user" needs no admin rights), ticking "Add Python to PATH".
2. Get the code into a normal folder such as `C:\OpsMonitor`, **not** OneDrive:
   `git clone https://github.com/<you>/ops-monitor C:\OpsMonitor` (or download the ZIP from GitHub).
3. Copy `config.local.toml.example` to `config.local.toml` and fill in your mail settings / passwords.
4. Double-click **`start.bat`**. The first run installs everything automatically.
5. Open `http://localhost:8080` on that PC. Team members open `http://<office-pc-name>:8080`.

Update later: `git pull`, then restart `start.bat` (new packages install automatically). Your `config.local.toml` and `data/` are not touched.

> IT: allow inbound TCP 8080 on this PC from the office LAN / VPN range.
> Keep the PC from sleeping. To start it automatically, add `start.bat` to Task Scheduler "At log on" (needed for Outlook mail mode) or "At startup".
> If pip is blocked by the office proxy: run `make_wheels.bat` on a PC with internet, copy the `wheels` folder next to `start.bat`.
> If the AAI site fails behind a proxy, set `[network] proxy`. Company SSL certificates are trusted automatically through the Windows store.

## Settings, links, mailboxes, logins → `config.toml` + `config.local.toml`
`config.toml` is the shared template (in git). **Passwords and real addresses go in `config.local.toml`**, which is never committed and overrides `config.toml`.

| Section | What |
|---|---|
| `[sources.aai]` | AAI AIM India NOTAM summaries. **Already working, no login needed.** |
| `[sources.email]` | NOTAM e-mail / briefing mailbox (IMAP host, user, password, sender filter) |
| `[sources.jetplan]` | JetPlan URL / user / password (direct connection added once access is confirmed) |
| `[sources.folder]` | Drop folder `data/inbox`: put any NOTAM `.txt` / `.pdf` / `.eml` here (e.g. JetPlan exports) |
| `[alerts.email]` | How alert mails are sent (`method`) + team recipients for "new NOTAM" alerts |
| `[graph]` | Microsoft 365 app login, only for `method = "graph"` |
| `[network]` | Office proxy, if needed |
| `[notam]` | Expiry warning (7 days), archive retention (365 days) |
| `[links]` | Quick links shown on the home page |

Restart (close the window, run `start.bat`) after editing.

## Mail
Microsoft 365 no longer accepts plain username/password logins for IMAP/SMTP, so choose a `method`:

| `method` | Needs | Notes |
|---|---|---|
| `outlook` (default) | Outlook open and signed in on the app PC | No IT request. Sends as the signed-in user; reads a folder such as `Inbox/NOTAM` (use an Outlook rule to move NOTAM mails there). |
| `smtp` | An SMTP relay from IT (e.g. port 25, `use_tls = false`, no login) | Sending only, most robust. For reading, plain IMAP works only where the server still allows it. |
| `graph` | IT registers an Entra ID app with `Mail.Send` + `Mail.ReadWrite` | The fully supported Microsoft 365 way, and it runs without anyone logged in. |

Press **✉ Test mail** on the NOTAM page to check the settings.

## How the NOTAM sheet works
- New NOTAMs are added at the **top**, with a **NEW** badge. All open browsers refresh by themselves within about 15 s.
- **Airports tab**: the list of airports to monitor. Add or remove airports by hand, or upload an Excel / CSV file and the ICAO codes in it are detected. Only these airports appear on the NOTAM sheets; if the list is empty, all airports are shown.
- **Action Needed?**: choose `Yes`, `No action needed` or `Extending`. Extending opens a box for the previous NOTAM number.
- **Action Taken**: type what was done. Press Enter or click away to save. Your name and the time are recorded.
- Colours:
  - ⬜ **White**: new, not reviewed.
  - 🟡 **Yellow**: action needed, but not done yet (or Extending without the previous NOTAM number).
  - 🟢 **Green**: action taken, no action needed, or extended.
  - 🔴 **Red**: expiring within 7 days.
- **Performance-critical**: aerodrome closed, declared distances, displaced threshold, obstacles, any navaid NOTAM, and RWY closed **due WIP**. RWY closed on its own is not critical.
- Cancelled (NOTAMC), replaced (NOTAMR), expired, or dropped from the latest AAI summary → moves automatically to **Cancelled / Expired**, kept for 1 year, then removed.
- 🗑 deletes a NOTAM from the sheet. It will not be imported again.
- Click a NOTAM number to see full details and the change history (who did what, when).
- **Export to Excel** downloads the current sheet with colours.
- **Add / paste NOTAM**: paste ICAO-format NOTAMs, e.g. copied from JetPlan.

## About the AAI source
AAI AIM publishes a **monthly NOTAM summary PDF** per FIR (Chennai, Delhi, Kolkata, Mumbai) and series (A, C, G). The app checks every hour and imports a new or updated file automatically. NOTAMs issued **during** the month arrive through the e-mail feed / JetPlan once those are configured.

## Files
```
config.toml          <- settings template (in git)
config.local.toml    <- this PC's passwords / overrides (NOT in git)
start.bat            <- start the app
app/                 <- server (Python / FastAPI); mail backends in app/mailer.py
static/              <- web pages
data/ops_monitor.db  <- the database (back this up)
data/inbox/          <- drop NOTAM files here
```
