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


def load() -> dict:
    """config.toml = shared template (in git); config.local.toml = this PC's passwords (never in git)."""
    with open(ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    local = ROOT / "config.local.toml"
    if local.exists():
        with open(local, "rb") as f:
            _merge(cfg, tomllib.load(f))
    return cfg


CFG = load()


def path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else ROOT / q
