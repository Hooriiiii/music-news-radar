"""`AGENTS.md` est une copie de `CLAUDE.md`, seul l'en-tête change.

Les deux fichiers ont divergé pendant des semaines : `AGENTS.md` portait encore
l'ancienne règle pop et la progression en trois étapes quand `CLAUDE.md` les
avait corrigées (relevé du cahier des charges v2, 2026-11-12). Un agent Codex
lisait donc une ligne éditoriale périmée. Ce test rougit dès que l'un bouge
sans l'autre.
"""

from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
ENTETE_CLAUDE = (
    "# CLAUDE.md\n\nThis file provides guidance to Claude Code (claude.ai/code) "
    "when working with code in this repository.\n"
)


def _corps(chemin: Path, separateur: str) -> str:
    texte = chemin.read_text(encoding="utf-8")
    assert separateur in texte, f"{chemin.name} : en-tête attendu introuvable"
    return texte.split(separateur, 1)[1]


def test_agents_md_reprend_claude_md_mot_pour_mot():
    claude = (RACINE / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith(ENTETE_CLAUDE)
    corps_claude = claude[len(ENTETE_CLAUDE):]

    corps_agents = _corps(RACINE / "AGENTS.md", "Un test vérifie que les deux restent identiques.\n")

    assert corps_agents == corps_claude, (
        "AGENTS.md a divergé de CLAUDE.md : modifier CLAUDE.md, puis régénérer "
        "AGENTS.md (même contenu, seul l'en-tête change)."
    )
