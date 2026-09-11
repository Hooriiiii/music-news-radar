"""Sentinelle : toutes les migrations doivent au moins s'IMPORTER.

Aucun test n'importait les migrations, si bien qu'un `op.f()` pose au niveau
module -- qui leve a l'import, le proxy Operations n'etant pas encore etabli --
a traverse la suite entiere au vert. Ce test ferme cet angle mort sans base de
donnees : il ne joue aucune migration, il les charge.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

# Chemin absolu derive de __file__ : un chemin relatif a "alembic.ini" ne
# resout que si pytest est lance depuis la racine du depot, ce qui n'est pas
# garanti (IDE, CI, ou simple `cd` avant la commande).
RACINE = Path(__file__).resolve().parent.parent
ALEMBIC_INI = RACINE / "alembic.ini"


def test_toutes_les_migrations_se_chargent():
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    revisions = list(script.walk_revisions())
    assert revisions, "aucune migration trouvee"
