"""Les reglages que le dashboard doit ANNONCER, derives et non recopies.

Un endpoint plutot qu'une injection au rendu : `app/static/dashboard.html` est
servi comme un fichier statique (`StaticFiles`), pas par un moteur de gabarits --
l'injecter exigerait de passer la page par Jinja2 au seul motif d'y coller deux
nombres, et le fichier cesserait d'etre lisible tel quel. Et le dashboard sait
deja lire un endpoint sans rien couter : celui-ci ne touche ni la base, ni le
reseau, ni un fichier.

Regle heritee du bandeau de supervision : une valeur qu'on n'a pas pu lire
s'affiche INCONNUE. Si cette requete echoue, le dashboard enleve le chiffre de sa
phrase -- il n'en invente jamais un.
"""

from fastapi import APIRouter

from app.config import settings
from app.schemas import ReglagesOut

router = APIRouter(tags=["reglages"])


@router.get("/reglages", response_model=ReglagesOut)
def lire_reglages():
    return ReglagesOut(
        retention_days=settings.retention_days,
        pipeline_intervalle_s=settings.pipeline_intervalle_s,
    )
