"""Ops Monitor web server.  Run with:  python -m app.main   (or start.bat)"""
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import airport_import, db, exporter, notifier, sources
from .config import CFG, ROOT
from .notam_parser import parse_any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_app):
    db.init()
    sources.start_scheduler()
    yield


app = FastAPI(title="Ops Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def user_of(req: Request) -> str:
    return unquote(req.headers.get("X-User", "")).strip()[:40] or "unknown"


# ---------------------------------------------------------------- pages
@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/notams")
def notams_page():
    return FileResponse(STATIC / "notams.html")


@app.get("/database")
def database_page():
    return FileResponse(STATIC / "database.html")


# ---------------------------------------------------------------- api
@app.get("/api/config")
def api_config():
    return {
        "title": CFG["app"]["title"], "team": CFG["app"]["team"],
        "links": {k: v for k, v in CFG.get("links", {}).items()},
        "expiring_days": CFG["notam"].get("expiring_days", 7),
        "retention_days": CFG["notam"].get("archive_retention_days", 365),
    }


@app.get("/api/version")
def api_version():
    return {"version": db.version()}


@app.get("/api/notams")
def api_notams(view: str = "active"):
    return db.list_notams("ARCHIVED" if view == "archive" else "ACTIVE")


@app.get("/api/notams/{nid}")
def api_notam(nid: int):
    r = db.get(nid)
    if not r:
        raise HTTPException(404)
    return r


class ActionIn(BaseModel):
    action_needed: str | None = None   # "YES" / "NO" / "EXT" (extending) / "" (clear)
    action_taken: str | None = None
    prev_notam: str | None = None      # NOTAM being extended


@app.post("/api/notams/{nid}/action")
def api_action(nid: int, body: ActionIn, req: Request):
    if body.action_needed not in (None, "", "YES", "NO", "EXT"):
        raise HTTPException(400, "action_needed must be YES, NO, EXT or empty")
    if not db.set_action(nid, body.action_needed, body.action_taken, user_of(req), body.prev_notam):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/notams/{nid}")
def api_delete(nid: int, req: Request):
    if not db.delete(nid, user_of(req)):
        raise HTTPException(404)
    return {"ok": True}


class PasteIn(BaseModel):
    text: str
    fir: str = ""


@app.post("/api/notams/paste")
def api_paste(body: PasteIn):
    found = parse_any(body.text, body.fir.strip().upper())
    added = db.ingest(found, "MANUAL")
    return {"found": len(found), "added": len(added)}


# ---------------------------------------------------------------- monitored airports
@app.get("/api/airports")
def api_airports():
    return db.list_airports()


class AirportIn(BaseModel):
    icao: str
    name: str = ""


class AirportsIn(BaseModel):
    airports: list[AirportIn]


@app.post("/api/airports")
def api_airports_add(body: AirportsIn, req: Request):
    return {"added": db.add_airports([a.model_dump() for a in body.airports], user_of(req))}


@app.delete("/api/airports/{icao}")
def api_airport_remove(icao: str):
    if not db.remove_airport(icao):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/airports")
def api_airports_clear():
    db.clear_airports()
    return {"ok": True}


@app.post("/api/airports/detect")
async def api_airports_detect(req: Request, filename: str):
    """Body = the raw Excel / CSV file. Returns the ICAO codes found, for the user to confirm."""
    data = await req.body()
    try:
        return airport_import.detect(filename, data, db.known_locations())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the file: {e}")


@app.post("/api/fetch")
def api_fetch():
    threading.Thread(target=sources.run_due, kwargs={"force": True}, daemon=True).start()
    return {"started": True}


@app.post("/api/test-mail")
def api_test_mail():
    try:
        return {"message": notifier.send_test()}
    except Exception as e:
        raise HTTPException(400, f"Mail failed: {e}")


@app.get("/api/sources")
def api_sources():
    status = {s["name"]: s for s in db.source_statuses()}
    out = []
    for name, (key, _) in sources.SOURCES.items():
        s = status.get(name, {})
        out.append({"name": name, "enabled": bool(CFG["sources"].get(key, {}).get("enabled")),
                    "last_run": s.get("last_run"), "ok": s.get("ok"), "message": s.get("message", "")})
    return out


@app.get("/api/export")
def api_export(view: str = "active"):
    archive = view == "archive"
    data = exporter.build(db.list_notams("ARCHIVED" if archive else "ACTIVE"), archive)
    name = f"NOTAMs_{'archive' if archive else 'active'}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


if __name__ == "__main__":
    uvicorn.run(app, host=CFG["app"].get("host", "0.0.0.0"), port=int(CFG["app"].get("port", 8080)))
