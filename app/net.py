"""Shared HTTP session that works behind an office proxy / SSL-inspecting firewall."""
import logging

import requests

from .config import CFG

log = logging.getLogger("net")

# Trust the Windows certificate store too, so a company root certificate
# (used by SSL-inspecting firewalls) is accepted by requests, IMAP and SMTP.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception as e:  # optional: plain certifi trust still works at home
    log.info("truststore not active: %s", e)

HTTP = requests.Session()
HTTP.headers["User-Agent"] = "Mozilla/5.0 (OpsMonitor NOTAM reader)"
_proxy = CFG.get("network", {}).get("proxy", "")
if _proxy:
    HTTP.proxies = {"http": _proxy, "https": _proxy}
