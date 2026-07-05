import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models import Source


@dataclass
class RawItem:
    """Item normalisé produit par un adapter. Le reste du pipeline ne connaît que ça."""

    url: str
    title: str
    summary: str | None = None
    published_at: dt.datetime | None = None
    media_urls: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    """Contrat commun à toutes les sources : fetch() -> list[RawItem]."""

    def __init__(self, source: Source):
        self.source = source

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """Récupère et normalise les items de la source."""
