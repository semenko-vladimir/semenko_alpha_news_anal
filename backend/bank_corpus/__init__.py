"""Local-only, explicitly indexed bank research retrieval; no import-time I/O."""

from .repository import BankPassage, BankRepository, CorpusError, build_index

__all__ = ["BankPassage", "BankRepository", "CorpusError", "build_index"]
