"""Detect ICAO airport codes in an uploaded Excel / CSV sheet."""
from __future__ import annotations

import csv
import io
import re

from openpyxl import load_workbook

ICAO = re.compile(r"^[A-Z]{4}$")
# 4-letter words that often appear in sheets but are not airports
NOT_ICAO = {"ICAO", "IATA", "NAME", "CITY", "CODE", "TYPE", "BASE", "MAIN", "AREA", "ZONE", "NOTE", "INTL",
            "YEAR", "DATE", "TIME", "LIST", "SNOW", "WEST", "EAST", "NONE", "OPEN", "SHUT", "DONE", "TRUE",
            "FIRS", "AIRP", "PORT", "ROAD", "INFO", "DATA", "MAXI", "MINI", "TOTAL", "LONG", "WIDE", "HIGH",
            "RWYS", "ELEV", "LAND", "TAKE", "TORA", "TODA", "ASDA", "PCN", "SLOPE", "WIND", "TEMP", "STAT"}


def _rows(filename: str, data: bytes) -> list[list[str]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True):
                rows.append(["" if v is None else str(v).strip() for v in r])
        return rows
    if name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", "ignore")
        return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text))]
    raise ValueError("Please upload an .xlsx or .csv file (old .xls: open in Excel and 'Save As' .xlsx)")


def detect(filename: str, data: bytes, known: dict[str, str]) -> list[dict]:
    rows = _rows(filename, data)

    # if a column is headed "ICAO ...", only read that column
    icao_col = None
    for r in rows[:10]:
        for i, v in enumerate(r):
            if "ICAO" in v.upper():
                icao_col = i
                break
        if icao_col is not None:
            break

    found: dict[str, dict] = {}
    for r in rows:
        cells = [(i, v.upper()) for i, v in enumerate(r) if v]
        for i, v in cells:
            if icao_col is not None and i != icao_col:
                continue
            if not ICAO.match(v) or v in NOT_ICAO or v in found:
                continue
            # name = first longer text cell on the same row
            name = next((r[j] for j, w in cells if j != i and len(w) > 4 and re.search(r"[A-Z]", w)
                         and not ICAO.match(w)), "")
            found[v] = {"icao": v, "name": name or known.get(v, ""), "has_notams": v in known}
    return sorted(found.values(), key=lambda x: x["icao"])
