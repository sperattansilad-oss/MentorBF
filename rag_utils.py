"""
ETAPE 3 : Fonctions de RECHERCHE (retrieval).

A chaque question de l'utilisateur, on va :
1. Transformer sa question en embedding (même modèle que pour l'index)
2. Comparer ce vecteur à ceux de toutes les fiches (similarité cosinus)
3. Garder les K fiches les plus proches
4. Renvoyer leur texte, pour l'injecter dans le prompt envoyé à l'IA

C'est ça, le "R" (Retrieval) du RAG : on va chercher l'info AVANT
de demander à l'IA de répondre, au lieu de compter sur sa mémoire.

FORMAT DE STOCKAGE (changé le 09/09/2026) : les embeddings ne sont plus
dans index.json (94 Mo en JSON texte, causait des timeouts au
déploiement) -- ils sont dans index_embeddings.npy (binaire compact,
~19 Mo) + index_meta.json (~3 Mo, sans le champ "embedding").

TÉLÉCHARGEMENT AUTOMATIQUE DEPUIS GITHUB (nouveau, 09/09/2026) : même
réduits à ~22 Mo au total, ces deux fichiers ont continué à faire
échouer systématiquement l'envoi via `rsconnect deploy` (timeouts /
erreurs SSL répétées, cause jamais identifiée avec certitude -- essayé
sur deux réseaux différents, avec et sans antivirus tiers). Solution
retenue : ces deux fichiers NE SONT PLUS envoyés du tout avec le reste
du code -- ils sont hébergés sur GitHub (dépôt "MentorBF" de l'auteur),
et ce module les télécharge lui-même au premier démarrage de l'app, via
une simple requête HTTPS classique (beaucoup plus fiable qu'un envoi de
gros fichier par rsconnect). Une fois téléchargés une première fois, ils
restent en cache sur le disque du serveur -- les démarrages suivants ne
retéléchargent rien tant que les fichiers sont déjà là.
"""
import json
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()
EMBEDDING_MODEL = "text-embedding-3-small"

META_PATH = Path("index_meta.json")
EMBEDDINGS_PATH = Path("index_embeddings.npy")

# ⚠️ Si tu régénères ces fichiers plus tard (nouvelle source ajoutée à
# mentors.json, 02_build_index.py relancé), pense à les ré-uploader sur
# ce même dépôt GitHub pour que ces URL restent à jour.
URL_META = "https://github.com/sperattansilad-oss/MentorBF/raw/refs/heads/main/index_meta.json"
URL_EMBEDDINGS = "https://github.com/sperattansilad-oss/MentorBF/raw/refs/heads/main/index_embeddings.npy"


def _telecharger_si_absent(url: str, chemin: Path):
    """Télécharge chemin depuis url UNIQUEMENT s'il n'existe pas déjà
    localement (évite de retélécharger à chaque redémarrage de l'app)."""
    if chemin.exists():
        return
    print(f"[rag_utils] {chemin} absent -- téléchargement depuis GitHub...")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    chemin.write_bytes(resp.content)
    print(f"[rag_utils] {chemin} téléchargé ({chemin.stat().st_size / 1e6:.1f} Mo).")


_telecharger_si_absent(URL_META, META_PATH)
_telecharger_si_absent(URL_EMBEDDINGS, EMBEDDINGS_PATH)

with open(META_PATH, encoding="utf-8") as f:
    _mentors = json.load(f)

_matrix = np.load(EMBEDDINGS_PATH)

# Garde-fou : les deux fichiers doivent être parfaitement synchronisés
# (même nombre de lignes, même ordre) -- sinon une fiche recevrait le
# score de similarité d'une AUTRE fiche par erreur, silencieusement.
if len(_mentors) != _matrix.shape[0]:
    raise RuntimeError(
        f"index_meta.json ({len(_mentors)} fiches) et index_embeddings.npy "
        f"({_matrix.shape[0]} lignes) ne correspondent pas -- relance "
        f"02_build_index.py pour régénérer les deux fichiers ensemble, "
        f"puis ré-uploade-les sur GitHub."
    )


def _embed(query: str) -> np.ndarray:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return np.array(resp.data[0].embedding)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / np.linalg.norm(a)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return b_norm @ a_norm


def retrieve(query: str, top_k: int = 5, universite_code=None) -> list:
    """
    universite_code : "UJKZ", "UNB", "UNZ", ou None (pas de filtre).
    Si un filtre est donne et qu'il ne reste AUCUNE fiche apres filtrage,
    on retombe automatiquement sur la recherche non filtree.
    """
    candidats = _mentors
    matrice = _matrix

    if universite_code and universite_code != "Toutes les universites":
        indices = [i for i, m in enumerate(_mentors) if m.get("universite_code") == universite_code]
        if indices:
            candidats = [_mentors[i] for i in indices]
            matrice = _matrix[indices]

    query_vec = _embed(query)
    scores = _cosine_similarity(query_vec, matrice)
    best_indices = np.argsort(scores)[::-1][:top_k]
    return [candidats[i] for i in best_indices]


def build_context(query: str, top_k: int = 5, universite_code=None) -> str:
    results = retrieve(query, top_k=top_k, universite_code=universite_code)
    parts = [f"--- Fiche {i+1} ---\n{m['text']}" for i, m in enumerate(results)]
    return "\n\n".join(parts)