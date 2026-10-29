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
import unicodedata
from pathlib import Path

import numpy as np
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()
EMBEDDING_MODEL = "text-embedding-3-small"

# Nombre de fiches transmises au chatbot : 5 pour une question libre, 10 dès
# qu'un filtre UFR/département/spécialité est actif (liste plus utile ; le
# bouton "Voir la liste" reste le moyen d'obtenir la liste COMPLETE, sans IA).
TOP_K_DEFAUT = 5
TOP_K_AVEC_FILTRE = 10
# Au-delà de 5 fiches, on tronque le texte de chaque fiche (certaines font
# plus de 16 000 caractères à cause de leurs publications) pour ne pas
# faire exploser la taille du message envoyé au modèle.
LONGUEUR_MAX_FICHE_LISTE = 3500

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


def _sans_accents(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()


def _normaliser_nom_departement(texte: str) -> str:
    """'Informatique (recherche fondamentale)' -> 'informatique' (accents,
    casse et parenthèses ignorés), pour comparer des départements dont le
    libellé varie d'une université à l'autre."""
    texte = _sans_accents(texte)
    texte = texte.split("(")[0]
    return " ".join(texte.replace("-", " ").split())


_VALEURS_VIDES = ("", "information non disponible", "a completer", "à compléter")

# Pré-calculs (une seule fois au démarrage) pour que la recherche élargie reste rapide.
_N_DEPT = [_normaliser_nom_departement(m.get("departement") or "") for m in _mentors]
_N_SPEC = [_sans_accents(m.get("specialite") or "") for m in _mentors]
_N_TETE = [_sans_accents((m.get("text") or "")[:1500]) for m in _mentors]


def _contient_mots(grand: str, petit: str) -> bool:
    """True si l'expression `petit` apparaît comme MOTS ENTIERS dans `grand`."""
    return bool(petit) and f" {petit} " in f" {grand} "


# Niveaux de la recherche en cascade : (filtres conservés, mode de comparaison du département).
# On tente d'abord TOUS les filtres choisis, puis on élargit étape par étape.
_NIVEAUX = [
    ({"universite", "ufr", "departement", "specialite"}, "exact"),
    ({"universite", "ufr", "departement", "specialite"}, "proche"),
    ({"ufr", "departement", "specialite"}, "exact"),       # autres universités, même UFR
    ({"ufr", "departement", "specialite"}, "proche"),
    ({"departement", "specialite"}, "proche"),             # autres universités, même département
    ({"departement"}, "proche"),                           # sans la spécialité
]

_NOMS_FILTRES = {
    "universite": "Université",
    "ufr": "UFR / Institut",
    "departement": "Département",
    "specialite": "Spécialité",
}


def retrieve_detail(query: str, top_k: int = TOP_K_DEFAUT, universite_code=None,
                    departement=None, specialite=None, ufr_predicat=None,
                    ufr_label=None, universite_alias=None,
                    strict_universite=False) -> dict:
    """
    Recherche EN CASCADE dans TOUTE la base (toutes les universités).

    1. On applique d'abord TOUS les filtres choisis par l'étudiant.
    2. Si aucune fiche ne correspond, on élargit étape par étape : même
       département dans la même université (libellé proche), puis les AUTRES
       universités avec le même UFR / le même département, puis le même
       département seul. Le résultat dit toujours quels filtres ont été
       abandonnés ("elargis"), pour que le chatbot l'explique à l'étudiant.
    3. Si rien ne correspond nulle part : recherche par proximité de sens sur
       toute la base ("aucun_filtre_possible").

    Cas particulier : seule une université est choisie (aucun UFR, département
    ni spécialité) et elle n'a aucune fiche (ex. UV-BF) : il n'y a pas de
    discipline sur laquelle élargir. Avec strict_universite=True (bouton
    "Rechercher") on renvoie ZÉRO fiche ; sinon (question libre) la question
    elle-même est recherchée sur toute la base.

    Une université sans fiche ÉTIQUETÉE est aussi cherchée par MENTION dans
    le texte des fiches (universite_alias, ex. ["uv-bf", "université virtuelle"]).

    Renvoie un dict : fiches, total_filtre, demandes (filtres choisis),
    appliques (filtres réellement appliqués), elargis (filtres abandonnés),
    top_k, universites (codes présents dans les fiches retenues),
    universite_sans_donnees (si cas particulier), aucun_filtre_possible.
    """
    univ_valide = bool(universite_code) and universite_code not in (
        "Toutes les universites", "Toutes les universités")
    dept_valide = bool(departement) and departement not in ("Tous les départements", "à compléter")
    spec_valide = bool(specialite) and specialite != "Toutes les spécialités"

    demandes = {}
    if univ_valide:
        demandes["universite"] = universite_code
    if ufr_predicat is not None:
        demandes["ufr"] = ufr_label or "UFR choisie"
    if dept_valide:
        demandes["departement"] = departement
    if spec_valide:
        demandes["specialite"] = specialite

    n_dept_demande = _normaliser_nom_departement(departement) if dept_valide else ""
    mots_univ = [_sans_accents(a) for a in (universite_alias or []) if a]

    # --- tests élémentaires, un par filtre : renvoient (correspond ?, force) ---
    def test_univ(i):
        m = _mentors[i]
        if m.get("universite_code") == universite_code:
            return True
        return bool(mots_univ) and any(a in _N_TETE[i] or a in _sans_accents(m.get("ufr") or "") for a in mots_univ)

    def test_ufr(i):
        return bool(ufr_predicat(_mentors[i]))

    def test_spec(i):
        return _mentors[i].get("specialite") == specialite

    def test_dept(i, mode):
        """Renvoie 0 (non), 1 (mentionné dans le texte seulement) ou 2 (champ renseigné)."""
        if _mentors[i].get("departement") == departement:
            return 2
        if mode == "exact":
            return 0
        nd = _N_DEPT[i]
        if nd and nd not in _VALEURS_VIDES and (_contient_mots(nd, n_dept_demande) or _contient_mots(n_dept_demande, nd)):
            return 2
        if n_dept_demande and _contient_mots(_N_SPEC[i], n_dept_demande):
            return 2
        if n_dept_demande and _contient_mots(_N_TETE[i], n_dept_demande):
            return 1
        return 0

    def candidats_niveau(conserves, mode):
        """Indices des fiches qui satisfont les filtres conservés, avec un bonus de rang."""
        sortie = []
        for i in range(len(_mentors)):
            bonus = 0.0
            if "universite" in conserves and "universite" in demandes and not test_univ(i):
                continue
            if "ufr" in conserves and "ufr" in demandes and not test_ufr(i):
                continue
            if "specialite" in conserves and "specialite" in demandes and not test_spec(i):
                continue
            if "departement" in conserves and "departement" in demandes:
                force = test_dept(i, mode)
                if force == 0:
                    continue
                bonus = 1.0 if force == 2 else 0.0
            sortie.append((i, bonus))
        return sortie

    retenus, appliques_cles, deja = None, None, set()
    if demandes:
        for conserves, mode in _NIVEAUX:
            cles = frozenset(conserves & set(demandes))
            if not cles or (cles, mode if "departement" in cles else "-") in deja:
                continue
            deja.add((cles, mode if "departement" in cles else "-"))
            trouves = candidats_niveau(cles, mode)
            if trouves:
                retenus, appliques_cles = trouves, set(cles)
                break

    aucun_filtre = False
    if retenus is None:
        solo_univ = set(demandes) == {"universite"}
        if solo_univ and strict_universite:
            return {
                "fiches": [], "total_filtre": 0, "demandes": demandes, "appliques": {},
                "elargis": {}, "top_k": top_k, "universites": [],
                "universite_sans_donnees": universite_code, "aucun_filtre_possible": False,
            }
        retenus = [(i, 0.0) for i in range(len(_mentors))]
        appliques_cles = set()
        aucun_filtre = bool(demandes)

    appliques = {k: demandes[k] for k in demandes if k in appliques_cles}
    elargis = {k: demandes[k] for k in demandes if k not in appliques_cles}
    if appliques_cles & {"ufr", "departement", "specialite"} and top_k < TOP_K_AVEC_FILTRE:
        top_k = TOP_K_AVEC_FILTRE

    indices = [i for i, _ in retenus]
    bonus = np.array([b for _, b in retenus])
    scores = _cosine_similarity(_embed(query), _matrix[indices]) + bonus
    meilleurs = np.argsort(scores)[::-1][:top_k]
    fiches = [_mentors[indices[j]] for j in meilleurs]
    return {
        "fiches": fiches,
        "total_filtre": len(retenus),
        "demandes": demandes,
        "appliques": appliques,
        "elargis": elargis,
        "top_k": top_k,
        "universites": sorted({m.get("universite_code") for m in fiches if m.get("universite_code")}),
        "universite_sans_donnees": None,
        "aucun_filtre_possible": aucun_filtre,
    }


def retrieve(query: str, top_k: int = TOP_K_DEFAUT, universite_code=None, departement=None,
             specialite=None, ufr_predicat=None, ufr_label=None, universite_alias=None,
             strict_universite=False) -> list:
    """Même chose que retrieve_detail(), mais ne renvoie que la liste de fiches
    (compatibilité avec l'ancien code)."""
    return retrieve_detail(
        query, top_k=top_k, universite_code=universite_code, departement=departement,
        specialite=specialite, ufr_predicat=ufr_predicat, ufr_label=ufr_label,
        universite_alias=universite_alias, strict_universite=strict_universite,
    )["fiches"]


def _liste_filtres(d: dict) -> str:
    return " ; ".join(f"{_NOMS_FILTRES[k]} = {v}" for k, v in d.items())


def _entete_portee(info: dict) -> str:
    """Bloc de texte, placé AVANT les fiches : il dit au modèle quels filtres
    l'étudiant a choisis, lesquels ont pu être appliqués, lesquels ont dû être
    abandonnés, et ce qu'il doit annoncer (voir prompt.txt, PORTÉE DE LA RECHERCHE)."""
    L = ["=== PORTEE DE LA RECHERCHE ==="]
    nb = len(info["fiches"])
    demandes, appliques, elargis = info["demandes"], info["appliques"], info["elargis"]

    if info.get("universite_sans_donnees"):
        L.append(
            f"AUCUNE FICHE : aucun enseignant n'est enregistre pour l'universite "
            f"{info['universite_sans_donnees']}, la recherche dans TOUTE la base n'a trouve aucune "
            "fiche qui la mentionne, et l'etudiant n'a choisi aucun UFR ni departement sur lequel "
            "elargir. Reponds par une phrase courte et naturelle : il n'y a pas (encore) "
            "d'enseignant pour cette universite dans la base. Ne cite aucun enseignant. "
            "Termine par la ligne MBF_SUGGESTIONS habituelle avec 3 questions generales."
        )
    elif info.get("aucun_filtre_possible"):
        L.append(f"Filtres choisis par l'etudiant : {_liste_filtres(demandes)}.")
        L.append(
            "AUCUNE fiche de la base ne correspond a ces filtres, meme en elargissant a toutes "
            "les universites. Les fiches ci-dessous sont seulement les plus proches par le sens "
            "de la question. CONSIGNE : dis-le clairement a l'etudiant (nomme les filtres choisis) "
            "et presente ces fiches comme des pistes approximatives, jamais comme des "
            "enseignants correspondant a ses filtres."
        )
    elif elargis:
        L.append(f"Filtres choisis par l'etudiant : {_liste_filtres(demandes)}.")
        L.append("AUCUNE fiche ne correspond a TOUS ces filtres a la fois.")
        L.append(
            f"Recherche elargie a TOUTE la base en gardant : {_liste_filtres(appliques)}. "
            f"Filtres abandonnes : {', '.join(_NOMS_FILTRES[k] for k in elargis)}."
        )
        L.append(
            f"Nombre de fiches correspondant a la recherche elargie : {info['total_filtre']} "
            f"(universites presentes dans les {nb} fiches ci-dessous : {', '.join(info['universites']) or 'aucune'})."
        )
        nom_univ = demandes.get("universite")
        cible = f" de {nom_univ}" if nom_univ else ""
        L.append(
            f"CONSIGNE : commence par dire clairement qu'aucun enseignant{cible} ne correspond "
            f"a TOUS les filtres choisis (nomme-les : {_liste_filtres(demandes)}). Puis presente "
            "les enseignants ci-dessous comme des PROPOSITIONS d'autres universites qui ont le "
            "meme departement (ou le meme UFR), en affichant bien la colonne Universite. "
            "Ne les presente JAMAIS comme appartenant a l'universite demandee."
        )
    else:
        if appliques:
            L.append(f"Filtres appliques strictement : {_liste_filtres(appliques)}.")
            L.append(f"Nombre de fiches de la base qui correspondent a ces filtres : {info['total_filtre']}.")
        else:
            L.append("Aucun filtre applique : recherche sur toute la base par proximite de sens.")

    if not info.get("universite_sans_donnees"):
        L.append(f"Fiches transmises ci-dessous : les {nb} plus pertinentes pour la question.")
        if info["total_filtre"] > nb and (appliques or elargis):
            L.append(
                f"Il y a donc {info['total_filtre'] - nb} autres fiches non montrees : invite "
                "l'etudiant a utiliser le bouton \"Voir la liste\" de la barre laterale."
            )
    L.append("=== FIN PORTEE ===")
    return "\n".join(L)


def build_context(query: str, top_k: int = TOP_K_DEFAUT, universite_code=None, departement=None,
                  specialite=None, ufr_predicat=None, ufr_label=None, universite_alias=None,
                  strict_universite=False) -> str:
    info = retrieve_detail(
        query, top_k=top_k, universite_code=universite_code, departement=departement,
        specialite=specialite, ufr_predicat=ufr_predicat, ufr_label=ufr_label,
        universite_alias=universite_alias, strict_universite=strict_universite,
    )
    tronquer = info["top_k"] > TOP_K_DEFAUT
    parts = []
    for i, m in enumerate(info["fiches"]):
        texte = m["text"]
        if tronquer and len(texte) > LONGUEUR_MAX_FICHE_LISTE:
            texte = texte[:LONGUEUR_MAX_FICHE_LISTE] + "\n[... fiche tronquee ...]"
        parts.append(f"--- Fiche {i+1} ---\n{texte}")
    return _entete_portee(info) + "\n\n" + "\n\n".join(parts)
