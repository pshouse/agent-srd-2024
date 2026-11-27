"""Build-time deterministic Rules IR compiler for 5e 2024 (SRD 5.2)."""

from .build import DATASET_ID, SCHEMA_VERSION, build_rules

__all__ = ["DATASET_ID", "SCHEMA_VERSION", "build_rules"]
