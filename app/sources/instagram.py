"""Adapter Instagram via `business_discovery` de l'API Graph de Meta.

Gratuit, contrairement a l'API X qu'il remplace. Contrepartie a connaitre : seuls
les comptes PROFESSIONNELS (business ou creator) sont visibles. Un artiste
emergent en profil perso -- soit une bonne part du deal-flow A&R -- n'est pas
atteignable par cette voie. Cette source COMPLETE les RSS, elle ne les remplace
pas.
"""

from app.sources.base import RawItem, SourceAdapter


class InstagramAdapter(SourceAdapter):
    def fetch(self) -> list[RawItem]:
        raise NotImplementedError
