import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _read(p: Path) -> dict:
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        hint = (" A [section] may appear only once; to switch the mail method change the"
                " method line instead of adding a second section." if "twice" in str(e) else "")
        raise SystemExit(f"\n  Settings file {p.name} has a mistake: {e}.{hint}\n  Fix it in Notepad and start again.\n")


def load() -> dict:
    """config.toml = shared template (in git); config.local.toml = this PC's passwords (never in git)."""
    cfg = _read(ROOT / "config.toml")
    local = ROOT / "config.local.toml"
    if local.exists():
        _merge(cfg, _read(local))
    return cfg


CFG = load()


def path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q
