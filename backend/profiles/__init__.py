"""Explicit, local company profiles; importing this package performs no I/O."""

from .repository import CompanyProfile, ProfileDataError, ProfileRepository

__all__ = ["CompanyProfile", "ProfileDataError", "ProfileRepository"]
