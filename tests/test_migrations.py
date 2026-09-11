"""Sentinelle : toutes les migrations doivent au moins s'IMPORTER.

Aucun test n'importait les migrations, si bien qu'un `op.f()` pose au niveau
module -- qui leve a l'import, le proxy Operations n'etant pas encore etabli --
a traverse la suite entiere au vert. Ce test ferme cet angle mort sans base de
donnees : il ne joue aucune migration, il les charge.
"""

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_toutes_les_migrations_se_chargent():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(script.walk_revisions())
    assert revisions, "aucune migration trouvee"
