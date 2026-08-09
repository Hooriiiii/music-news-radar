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
    # Medias TYPES et non une liste d'URL : une video et sa vignette ne se
    # distinguaient pas, ce qui rendait le format REELS indecidable.
    # {"type": "photo"|"video"|"gif", "url": str,
    #  "preview_url": str | None, "duration_ms": int | None,
    #  "width": int | None, "height": int | None}
    media: list[dict] = field(default_factory=list)


class SourceAdapter(ABC):
    """Contrat commun à toutes les sources : fetch() -> list[RawItem]."""

    def __init__(self, source: Source):
        self.source = source

    @abstractmethod
    def fetch(self) -> list[RawItem]:
        """Récupère et normalise les items de la source."""
