"""E-mail alerts to the team when new NOTAMs arrive."""
from datetime import datetime
from html import escape

from . import db, mailer
from .config import CFG


def _cfg() -> dict:
    return CFG.get("alerts", {}).get("email", {})


def send_new_notams(added: list[dict]) -> None:
    cfg = _cfg()
    if not cfg.get("enabled") or not cfg.get("recipients"):
        return
    watched = db.airport_set()
    if watched:
        added = [a for a in added if a["location"] in watched]
    if cfg.get("alert_on", "critical") == "critical":
        added = [a for a in added if a["critical"]]
    if not added:
        return

    rows = "".join(
        f"<tr><td><b>{escape(a['notam_id'])}</b></td><td>{escape(a['location'] or '')}</td>"
        f"<td>{escape(', '.join(a['tags']))}</td><td>{escape(a['text'][:400])}</td></tr>"
        for a in added)
    mailer.send(
        cfg,
        f"[{CFG['app']['title']}] {len(added)} new NOTAM(s) - please review",
        "\n\n".join(f"{a['notam_id']} {a['location']}\n{a['text']}" for a in added),
        "<p>New NOTAMs need review on the Ops Monitor dashboard:</p>"
        "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse;font-family:Arial;font-size:12px'>"
        f"<tr style='background:#eee'><th>NOTAM</th><th>Location</th><th>Tags</th><th>Text</th></tr>{rows}</table>")


def send_test() -> str:
    """Send a test mail with the current settings (even if alerts are switched off)."""
    cfg = _cfg()
    now = f"{datetime.now():%d %b %Y %H:%M}"
    mailer.send(cfg, f"[{CFG['app']['title']}] Test mail",
                f"Ops Monitor mail settings work ({now}).",
                f"<p>Ops Monitor mail settings work ({escape(now)}).</p>")
    return f"Test mail sent to {', '.join(cfg['recipients'])} via {cfg.get('method', 'smtp')}"
