import logging
import time

from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# `pool_pre_ping` : un SELECT 1 avant de rendre une connexion du pool, et
# remplacement transparent si elle est morte. Neon est un Postgres serverless qui
# ferme vite les connexions inactives, et la cascade de scoring a rendu ces temps
# morts bien plus longs -- chaque article passe desormais quelques secondes dans
# le modele LOCAL, connexion ouverte et inactive pendant ce temps. Sans ce
# reglage, un run complet finissait en `PendingRollbackError` : la connexion
# tombait pendant que le modele reflechissait, et la session ne s'en relevait pas.
# `pool_recycle` fait le meme travail en amont, en jetant les connexions avant
# que le serveur ne les juge trop vieilles.
engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,
    pool_recycle=300,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Un job launchd part au reveil du Mac, parfois avant que la resolution DNS
# reponde. L'agent `digest` echouait ainsi chaque matin depuis juillet sur un
# « failed to resolve host ... .neon.tech » : quelques essais espaces valent
# mieux qu'un run perdu, et un echec explicite vaut mieux qu'une trace psycopg.
ATTENTE_ESSAIS = 5
ATTENTE_DELAI = 6.0


class BaseInjoignable(RuntimeError):
    """Levee quand la base reste injoignable apres tous les essais."""


def _ping() -> None:
    with engine.connect() as connexion:
        connexion.execute(text("SELECT 1"))


def attendre_base(
    essais: int = ATTENTE_ESSAIS,
    delai: float = ATTENTE_DELAI,
    dormir=time.sleep,
    connecter=None,
) -> None:
    """Attend que la base reponde, ou leve BaseInjoignable.

    `connecter` et `dormir` sont injectables : les tests ne touchent donc ni la
    base ni l'horloge. C'est la seule raison pour laquelle cette fonction, qui
    n'a qu'un usage en production, en accepte deux.
    """
    connecter = connecter or _ping
    derniere: Exception | None = None
    for essai in range(1, essais + 1):
        try:
            connecter()
            return
        except OperationalError as exc:
            derniere = exc
            if essai < essais:
                logger.warning(
                    "base injoignable (essai %d/%d), nouvel essai dans %.0f s",
                    essai,
                    essais,
                    delai,
                )
                dormir(delai)
    raise BaseInjoignable(
        f"base injoignable apres {essais} essais espaces de {delai:.0f} s"
    ) from derniere


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
