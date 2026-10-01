"""
Pydantic-Settings based configuration for Operation Abhedya-Chakra.

All values are read from environment variables or a .env file.
Nothing is hardcoded — change behaviour by setting env vars or editing .env.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration for Phase 1."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Data paths ---
    data_raw_dir: Path = Path("data/raw")
    data_processed_dir: Path = Path("data/processed")
    data_parquet_dir: Path = Path("data/parquet")
    data_benchmarks_dir: Path = Path("data/benchmarks")

    # --- DuckDB ---
    db_path: Path = Path("data/processed/abhedya.duckdb")
    duckdb_memory_limit: str = "10GB"
    duckdb_threads: int = 4
    duckdb_temp_dir: str | None = None  # None → DuckDB default

    # --- Schema ---
    schema_version: int = 1

    # --- Validation ---
    # Regex applied to Sender_Account and Receiver_Account
    account_regex: str = r"^\d{12}$"

    # --- Benchmark ---
    benchmark_threshold_rows_per_sec: int = 30_000

    # --- Logging ---
    log_level: str = "INFO"

    # ------------------------------------------------------------------ #
    # Validators                                                           #
    # ------------------------------------------------------------------ #

    @field_validator("account_regex")
    @classmethod
    def _validate_account_regex(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"account_regex is not a valid Python regex: {exc}") from exc
        return v

    @field_validator("duckdb_threads")
    @classmethod
    def _validate_threads(cls, v: int) -> int:
        if v < 1:
            raise ValueError("duckdb_threads must be >= 1")
        return v

    @model_validator(mode="after")
    def _ensure_directories(self) -> "Settings":
        """Create output directories if they do not exist yet."""
        for attr in (
            "data_raw_dir",
            "data_processed_dir",
            "data_parquet_dir",
            "data_benchmarks_dir",
        ):
            path: Path = getattr(self, attr)
            path.mkdir(parents=True, exist_ok=True)
        # Ensure DB parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def duckdb_config(self) -> dict:
        """Return kwargs dict for duckdb.connect()."""
        cfg: dict = {
            "database": str(self.db_path),
        }
        return cfg

    def duckdb_pragmas(self) -> list[str]:
        """
        Return a list of PRAGMA / SET statements to run immediately after
        opening a DuckDB connection.
        """
        stmts: list[str] = [
            f"SET memory_limit='{self.duckdb_memory_limit}';",
            f"SET threads={self.duckdb_threads};",
        ]
        if self.duckdb_temp_dir:
            # Path must be single-quoted inside SQL; escape embedded quotes
            safe = self.duckdb_temp_dir.replace("'", "''")
            stmts.append(f"SET temp_directory='{safe}';")
        return stmts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
