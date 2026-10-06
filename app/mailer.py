"""Send and read mail through one of three backends, chosen with `method` in config.toml:

  "smtp"    - SMTP / IMAP server (e.g. an internal relay from IT)
  "outlook" - the Outlook app signed in on this PC (Windows only, Outlook must be running)
  "graph"   - Microsoft 365 via Microsoft Graph (IT registers an app, see [graph] in config.toml)
"""
from __future__ import annotations

import email
import imaplib
import shutil
import smtplib
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from pathlib import Path

from .config import CFG
from .net import HTTP


@dataclass
class Mail:
    sender: str
    subject: str
    mark_read: Callable[[], None]
    raw: bytes | None = None                 # full MIME message (smtp / graph)
    body: str = ""                           # plain text body (outlook)
    attachments: list[tuple[str, bytes]] = field(default_factory=list)   # (file name, data) (outlook)


# ---------------------------------------------------------------- send
def send(cfg: dict, subject: str, text: str, html: str) -> None:
    to = cfg.get("recipients") or []
    if not to:
        raise RuntimeError("No recipients set in config")
    method = cfg.get("method", "smtp")
    if method == "outlook":
        _outlook_send(cfg, to, subject, html)
    elif method == "graph":
        _graph_send(cfg, to, subject, html)
    else:
        _smtp_send(cfg, to, subject, text, html)


def _smtp_send(cfg: dict, to: list[str], subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_addr") or cfg["username"]
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    port = int(cfg.get("smtp_port", 587))
    if port == 465:
        s = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30)
    else:
        s = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
        if cfg.get("use_tls", True):
            s.starttls()
    with s:
        if cfg.get("username") and cfg.get("password"):   # an internal relay usually needs no login
            s.login(cfg["username"], cfg["password"])
        s.send_message(msg)


# ---------------------------------------------------------------- read
def fetch_unread(cfg: dict) -> Iterator[Mail]:
    method = cfg.get("method", "smtp")
    if method == "outlook":
        yield from _outlook_unread(cfg)
    elif method == "graph":
        yield from _graph_unread(cfg)
    else:
        yield from _imap_unread(cfg)


def _imap_unread(cfg: dict) -> Iterator[Mail]:
    if not cfg.get("username"):
        raise RuntimeError("Mailbox not configured in config.toml")
    box = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)))
    try:
        box.login(cfg["username"], cfg["password"])
        box.select(cfg.get("folder", "INBOX"))
        _, ids = box.search(None, "UNSEEN")
        for i in ids[0].split():
            _, msg_data = box.fetch(i, "(BODY.PEEK[])")     # peek = mail stays unread unless we use it
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw, policy=policy.default)
            yield Mail(str(msg.get("From", "")), str(msg.get("Subject", "")),
                       lambda i=i: box.store(i, "+FLAGS", "\\Seen"), raw=raw)
    finally:
        try:
            box.logout()
        except Exception:
            pass


# ---------------------------------------------------------------- outlook (Windows)
def _outlook():
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise RuntimeError("Outlook mode needs Windows + pywin32 (pip install pywin32)")
    pythoncom.CoInitialize()              # needed in every background thread
    return win32com.client.Dispatch("Outlook.Application")


def _outlook_send(cfg: dict, to: list[str], subject: str, html: str) -> None:
    item = _outlook().CreateItem(0)       # 0 = mail item
    item.To = "; ".join(to)
    item.Subject = subject
    item.HTMLBody = html
    if cfg.get("from_addr"):
        item.SentOnBehalfOfName = cfg["from_addr"]   # shared mailbox, if you have "send as" rights
    item.Send()


def _outlook_folder(ns, name: str):
    """"INBOX" = default inbox; "Inbox/NOTAM" = sub-folder of the inbox."""
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    folder = ns.GetDefaultFolder(6)       # 6 = Inbox
    if parts and parts[0].lower() == "inbox":
        parts = parts[1:]
    for p in parts:
        folder = folder.Folders[p]
    return folder


def _outlook_unread(cfg: dict) -> Iterator[Mail]:
    ns = _outlook().GetNamespace("MAPI")
    folder = _outlook_folder(ns, cfg.get("folder", "INBOX"))
    items = [it for it in folder.Items.Restrict("[UnRead] = True") if getattr(it, "Class", 0) == 43]  # 43 = mail
    tmp = Path(tempfile.mkdtemp(prefix="opsmon_"))
    try:
        yield from _outlook_mails(items, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _outlook_mails(items, tmp: Path) -> Iterator[Mail]:
    for it in items:
        atts = []
        for k in range(1, it.Attachments.Count + 1):
            a = it.Attachments.Item(k)
            f = tmp / f"{k}_{a.FileName}"
            try:
                a.SaveAsFile(str(f))
                atts.append((a.FileName, f.read_bytes()))
            except Exception:
                pass                    # embedded images etc.
            finally:
                f.unlink(missing_ok=True)

        def mark(it=it):
            it.UnRead = False
            it.Save()
        yield Mail(f"{it.SenderName} <{it.SenderEmailAddress}>", it.Subject or "", mark,
                   body=it.Body or "", attachments=atts)


# ---------------------------------------------------------------- Microsoft Graph
GRAPH = "https://graph.microsoft.com/v1.0"


def _graph_token() -> str:
    g = CFG.get("graph", {})
    if not (g.get("tenant_id") and g.get("client_id") and g.get("client_secret")):
        raise RuntimeError("Fill in [graph] tenant_id / client_id / client_secret in config")
    r = HTTP.post(f"https://login.microsoftonline.com/{g['tenant_id']}/oauth2/v2.0/token", data={
        "grant_type": "client_credentials", "client_id": g["client_id"],
        "client_secret": g["client_secret"], "scope": "https://graph.microsoft.com/.default"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _graph_send(cfg: dict, to: list[str], subject: str, html: str) -> None:
    sender = cfg.get("from_addr") or cfg.get("username")
    if not sender:
        raise RuntimeError("Set from_addr (the mailbox that sends the alerts)")
    r = HTTP.post(f"{GRAPH}/users/{sender}/sendMail",
                  headers={"Authorization": f"Bearer {_graph_token()}"}, timeout=30, json={
                      "message": {"subject": subject, "body": {"contentType": "HTML", "content": html},
                                  "toRecipients": [{"emailAddress": {"address": a}} for a in to]},
                      "saveToSentItems": False})
    r.raise_for_status()


def _graph_unread(cfg: dict) -> Iterator[Mail]:
    mbx = cfg.get("username")
    if not mbx:
        raise RuntimeError("Set username (the NOTAM mailbox address) in config")
    auth = {"Authorization": f"Bearer {_graph_token()}"}
    folder = cfg.get("folder", "INBOX")
    folder = "inbox" if folder.upper() == "INBOX" else folder
    url = f"{GRAPH}/users/{mbx}/mailFolders/{folder}/messages?$filter=isRead eq false&$select=id&$top=50"
    ids = []          # list all first: marking mails read while paging would shift the pages
    while url:
        page = HTTP.get(url, headers=auth, timeout=60)
        page.raise_for_status()
        page = page.json()
        ids += [m["id"] for m in page.get("value", [])]
        url = page.get("@odata.nextLink")
    for mid in ids:
        raw = HTTP.get(f"{GRAPH}/users/{mbx}/messages/{mid}/$value", headers=auth, timeout=120)
        raw.raise_for_status()
        msg = email.message_from_bytes(raw.content, policy=policy.default)

        def mark(mid=mid):
            HTTP.patch(f"{GRAPH}/users/{mbx}/messages/{mid}", headers=auth,
                       json={"isRead": True}, timeout=30).raise_for_status()
        yield Mail(str(msg.get("From", "")), str(msg.get("Subject", "")), mark, raw=raw.content)
