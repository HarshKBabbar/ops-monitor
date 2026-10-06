"""NOTAM sources (AAI AIM, e-mail, drop folder, JetPlan) and the background scheduler."""
from __future__ import annotations

import email
import hashlib
import io
import logging
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from email import policy

from bs4 import BeautifulSoup
from pypdf import PdfReader

from . import db, mailer, notifier
from .config import CFG, path
from .net import HTTP
from .notam_parser import FIR_BY_NAME, parse_aai_summary, parse_any

log = logging.getLogger("sources")


# ---------------------------------------------------------------- text helpers
def pdf_text(data: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def eml_text(data: bytes) -> str:
    msg = email.message_from_bytes(data, policy=policy.default)
    parts = []
    for part in msg.walk():
        ctype = part.get_content_type()
        fname = (part.get_filename() or "").lower()
        try:
            if fname.endswith(".pdf") or ctype == "application/pdf":
                parts.append(pdf_text(part.get_payload(decode=True)))
            elif fname.endswith(".txt") or (ctype == "text/plain" and not fname):
                parts.append(part.get_content())
            elif ctype == "text/html" and not fname:
                parts.append(BeautifulSoup(part.get_content(), "html.parser").get_text("\n"))
        except Exception as e:  # one bad attachment should not lose the mail
            log.warning("attachment skipped: %s", e)
    return "\n".join(parts)


# ---------------------------------------------------------------- sources
def run_aai() -> tuple[int, str]:
    cfg = CFG["sources"]["aai"]
    html = HTTP.get(cfg["summary_page"], timeout=60).text
    files: dict[tuple[str, str], tuple[int, int, str]] = {}
    for url in set(re.findall(r'https?://[^"\']+?/notam_files/[^"\']+?\.pdf', html)):
        m = re.search(r"/(\w+?)_([A-Z])_(\d{4})_(\d{2})\.pdf$", url)
        if not m or m.group(1) not in cfg["firs"] or m.group(2) not in cfg["series"]:
            continue
        k, ym = (m.group(1), m.group(2)), (int(m.group(3)), int(m.group(4)))
        if k not in files or ym > files[k][:2]:
            files[k] = (*ym, url)          # keep only the latest month per FIR + series

    new_total, checked, archived = 0, 0, 0
    for (fir_name, series), (yy, mm, url) in sorted(files.items()):
        data = HTTP.get(url, timeout=120).content
        sha = hashlib.sha256(data).hexdigest()
        checked += 1
        if db.file_seen(url, sha):
            continue
        fir = FIR_BY_NAME.get(fir_name.upper(), "")
        summary = parse_aai_summary(pdf_text(data), fir=fir, series=series)
        added = db.ingest(summary.notams, "AAI")
        archived += db.reconcile_aai_summary(fir, series, summary.checklist, datetime(yy, mm, 1, tzinfo=timezone.utc))
        db.mark_file(url, sha, len(summary.notams))
        new_total += len(added)
        _alert(added)
    return new_total, f"{checked} summary files checked, {new_total} new, {archived} moved to archive"


def mail_text(m: mailer.Mail) -> str:
    if m.raw is not None:
        return eml_text(m.raw)
    parts = [m.body]
    for name, data in m.attachments:
        try:
            if name.lower().endswith(".pdf"):
                parts.append(pdf_text(data))
            elif name.lower().endswith(".txt"):
                parts.append(data.decode("utf-8", "ignore"))
            elif name.lower().endswith(".eml"):
                parts.append(eml_text(data))
        except Exception as e:  # one bad attachment should not lose the mail
            log.warning("attachment skipped: %s", e)
    return "\n".join(parts)


def run_email() -> tuple[int, str]:
    cfg = CFG["sources"]["email"]
    new_total, mails = 0, 0
    for m in mailer.fetch_unread(cfg):
        sender, subject = m.sender.lower(), m.subject
        if cfg.get("from_filter") and not any(f.lower() in sender for f in cfg["from_filter"]):
            continue
        if cfg.get("subject_filter") and cfg["subject_filter"].lower() not in subject.lower():
            continue
        mails += 1
        added = db.ingest(parse_any(mail_text(m), cfg.get("default_fir", "")), "EMAIL")
        m.mark_read()
        new_total += len(added)
        _alert(added)
    return new_total, f"{mails} mails read, {new_total} new NOTAMs"


def run_folder() -> tuple[int, str]:
    inbox = path(CFG["sources"]["folder"]["path"])
    done = inbox.parent / "processed"
    inbox.mkdir(parents=True, exist_ok=True)
    done.mkdir(parents=True, exist_ok=True)
    new_total, files = 0, 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file():
            continue
        data = f.read_bytes()
        ext = f.suffix.lower()
        text = pdf_text(data) if ext == ".pdf" else eml_text(data) if ext == ".eml" else data.decode("utf-8", "ignore")
        src = "JETPLAN" if "jetplan" in f.name.lower() or "jepp" in f.name.lower() else "FILE"
        added = db.ingest(parse_any(text), src)
        new_total += len(added)
        files += 1
        _alert(added)
        shutil.move(str(f), done / f"{datetime.now():%Y%m%d-%H%M%S}_{f.name}")
    return new_total, f"{files} files read, {new_total} new NOTAMs"


def run_jetplan() -> tuple[int, str]:
    raise RuntimeError("JetPlan connection not set up yet - use the drop folder for JetPlan exports")


SOURCES = {
    "AAI AIM": ("aai", run_aai),
    "E-mail feed": ("email", run_email),
    "Drop folder": ("folder", run_folder),
    "JetPlan": ("jetplan", run_jetplan),
}

# ---------------------------------------------------------------- alerts
_quiet = False   # first ever import: don't e-mail thousands of "new" NOTAMs


def _alert(added: list[dict]) -> None:
    if added and not _quiet:
        try:
            notifier.send_new_notams(added)
        except Exception as e:
            log.warning("alert mail failed: %s", e)


# ---------------------------------------------------------------- scheduler
_last_run: dict[str, float] = {}
_run_lock = threading.Lock()


def run_source(name: str) -> None:
    key, fn = SOURCES[name]
    _last_run[name] = time.time()
    try:
        _, msg = fn()
        db.set_source_status(name, True, msg)
    except Exception as e:
        log.exception("%s failed", name)
        db.set_source_status(name, False, str(e)[:300])


def run_due(force: bool = False) -> None:
    global _quiet
    if not _run_lock.acquire(blocking=False):
        return
    try:
        _quiet = db.is_empty()
        for name, (key, _) in SOURCES.items():
            cfg = CFG["sources"].get(key, {})
            if not cfg.get("enabled"):
                continue
            every = float(cfg.get("check_every_min", 15)) * 60
            if force or time.time() - _last_run.get(name, 0) >= every:
                run_source(name)
        db.housekeep()
        if _quiet and not db.is_empty():
            db.set_baseline()
    finally:
        _quiet = False
        _run_lock.release()


def start_scheduler() -> None:
    def loop():
        while True:
            try:
                run_due()
            except Exception:
                log.exception("scheduler")
            time.sleep(30)
    threading.Thread(target=loop, daemon=True, name="notam-scheduler").start()
