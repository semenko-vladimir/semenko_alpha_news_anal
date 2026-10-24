"""Offline conversion from source adapters to analysis evidence."""

from .source_bridge import BridgeError, BridgeResult, EventSourceRecord, normalize

__all__ = ["BridgeError", "BridgeResult", "EventSourceRecord", "normalize"]
