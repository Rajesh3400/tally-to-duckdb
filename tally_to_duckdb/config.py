"""Settings from environment variables (T2D_*), optionally a TOML file.

Call get_settings() at the point of use; tests swap settings with
get_settings.cache_clear(). No secrets live in code: the Tally basic-auth
password (only needed behind a proxy) comes from the environment.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = PKG_DIR.parent


def data_dir(name: str) -> Path:
    """`schema/` or `tally/`: inside the package when installed as a wheel,
    at the project root in a source checkout."""
    inside = PKG_DIR / name
    return inside if inside.is_dir() else ROOT_DIR / name


@dataclass
class Settings:
    db_path: str = "tally.duckdb"
    tally_host: str = "localhost"
    tally_port: int = 9000
    tally_protocol: str = "http"        # http | https
    tally_user: str = ""
    tally_password: str = ""
    # DuckDB holds its working set in memory up to this limit, then spills to
    # a duckdb_tmp folder next to the database file.
    duckdb_memory_limit: str = "600MB"
    duckdb_threads: int = 2


_ENV = {"T2D_DB": "db_path", "T2D_TALLY_HOST": "tally_host", "T2D_TALLY_PORT": "tally_port",
        "T2D_TALLY_PROTOCOL": "tally_protocol", "T2D_TALLY_USER": "tally_user",
        "T2D_TALLY_PASSWORD": "tally_password", "T2D_MEMORY_LIMIT": "duckdb_memory_limit",
        "T2D_THREADS": "duckdb_threads"}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    cfg = os.environ.get("T2D_CONFIG")
    values = tomllib.loads(Path(cfg).read_text(encoding="utf-8")) if cfg else {}
    for k, v in values.items():
        if hasattr(s, k):
            setattr(s, k, type(getattr(s, k))(v))
    for env, attr in _ENV.items():          # environment beats the file
        if env in os.environ:
            setattr(s, attr, type(getattr(s, attr))(os.environ[env]))
    return s
