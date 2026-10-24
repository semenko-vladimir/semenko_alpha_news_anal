"""Local, bounded CBR press-news ingestion and repository."""

from .repository import EventRecord, NewsRepository, NewsService, NewsServiceError

__all__ = ["EventRecord", "NewsRepository", "NewsService", "NewsServiceError"]
