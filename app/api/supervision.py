"""Le bandeau de supervision : un seul endpoint, aucune depense.

UN ENDPOINT DEDIE plutot que l'elargissement de cinq schemas. Trois raisons.
(1) La question posee est TRANSVERSALE -- « la machine tourne-t-elle ? » ne se
lit ni dans un article, ni dans un creneau, ni dans une source, mais dans les
trois a la fois plus quatre fichiers d'etat ; l'etaler sur cinq schemas
obligerait le dashboard a cinq requetes et a recomposer le verdict cote
navigateur, ou chaque lecture ratee redeviendrait un silence. (2) Le niveau
global doit etre calcule a UN endroit, parce que la propriete qui compte est
negative (« jamais vert si quelque chose est inconnu ») et qu'une regle negative
repartie sur cinq vues n'est verrouillable nulle part. (3) Le bandeau est
rafraichi en permanence, panneau ouvert ou non : une seule reponse compacte, pas
cinq listes completes.

Une exception ne remonte jamais en 500 : `superviser` rend des voyants
« inconnu ». Un bandeau vide se lirait « rien a signaler ».
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import SupervisionOut
from app.supervision import superviser

router = APIRouter(tags=["supervision"])


@router.get("/supervision", response_model=SupervisionOut)
def lire_supervision(db: Session = Depends(get_db)):
    return superviser(db)
