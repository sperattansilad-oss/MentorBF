from dotenv import load_dotenv
_ = load_dotenv()
import json
import re
import importlib
import chatlas as ctl
from shiny.express import ui, input, session
from shiny import render, reactive
from shiny.ui import output_ui  # <-- NOUVEAU : shiny.express.ui n'expose pas
                                 # output_ui (l'auto-placement Express ne
                                 # marche que pour du contenu top-level,
                                 # pas pour du contenu imbriqué dans une
                                 # modale/div construite dynamiquement) --
                                 # on utilise donc le output_ui() classique
                                 # de shiny.ui, qui fonctionne très bien
                                 # avec des fonctions décorées @render.ui
                                 # (importé depuis shiny, pas shiny.express,
                                 # voir juste au-dessus) peu importe le
                                 # style du reste du fichier.
import requests
from rag_utils import build_context   # <-- on importe la recherche
import auth_utils as auth             # <-- NOUVEAU : comptes + historique
from pays_monde import PAYS_MONDE     # <-- NOUVEAU : liste des pays pour le profil
from pypdf import PdfReader           # <-- NOUVEAU : extraction de texte des PDF envoyés
import os
import secrets
from urllib.parse import urlencode, parse_qs

# =========================================================================
# CONNEXION AVEC GOOGLE (nouveau, 08/09/2026)
#
# ARCHITECTURE CHOISIE : plutôt que d'ajouter une route serveur
# personnalisée (/auth/google/callback), ce qui demanderait de sortir du
# style Shiny Express "un seul fichier" (il faudrait un fichier app.py
# séparé qui enveloppe Mentors.py dans une appli Starlette avec des
# routes en plus -- change la commande de lancement, plus fragile), on
# utilise une astuce plus simple et 100% compatible avec
# "shiny run Mentors.py" tel quel : Google redirige directement vers
# L'URL RACINE de l'appli (ex: http://127.0.0.1:8001/), avec ?code=...
# dans l'URL -- et Shiny sait déjà lire ça nativement via
# session.clientdata.url_search() (API publique documentée), sans avoir
# besoin d'une route dédiée.
#
# ⚠️ IMPORTANT CÔTÉ GOOGLE CLOUD CONSOLE : il faut que l'URI de
# redirection enregistrée soit EXACTEMENT l'URL racine de l'appli, PAS
# .../auth/google/callback comme configuré au tout départ -- ajoute
# (en plus, pas besoin de retirer l'ancienne) cette URI dans "URI de
# redirection autorisées" :
#     http://127.0.0.1:8001/
# (adapte le port si tu lances l'app sur un port différent de 8001)
# =========================================================================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8001/")

URL_AUTORISATION_GOOGLE = "https://accounts.google.com/o/oauth2/v2/auth"
URL_JETON_GOOGLE = "https://oauth2.googleapis.com/token"
URL_USERINFO_GOOGLE = "https://www.googleapis.com/oauth2/v3/userinfo"


def _google_active() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def _url_connexion_google(state: str) -> str:
    """Construit l'URL vers laquelle rediriger le navigateur pour lancer
    la connexion Google (bouton <a>, PAS un bouton Shiny -- doit être une
    vraie navigation de page, pas une requête AJAX)."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{URL_AUTORISATION_GOOGLE}?{urlencode(params)}"


def _echanger_code_google(code: str) -> dict:
    """Échange le code d'autorisation contre un jeton d'accès (appel
    serveur-à-serveur, jamais exposé au navigateur)."""
    resp = requests.post(
        URL_JETON_GOOGLE,
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _infos_utilisateur_google(access_token: str) -> dict:
    """Récupère email/nom/photo depuis le jeton d'accès."""
    resp = requests.get(
        URL_USERINFO_GOOGLE,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

# =========================================================================
# Structure OFFICIELLE des filières (= départements) par UFR/institut,
# importée directement depuis 20_Extraire_departement.py -- SOURCE UNIQUE
# DE VÉRITÉ, pas de duplication. Le menu "Département" de la sidebar
# affiche désormais TOUTES les filières connues du document officiel,
# même si aucun enseignant n'y est encore rattaché avec succès dans
# mentors.json -- exactement comme Université/UFR (cohérence demandée).
# =========================================================================
_module_departements = importlib.import_module("20_Extraire_departement")
ORGANIGRAMME = _module_departements.ORGANIGRAMME
DEPARTEMENTS_INSTITUTS = _module_departements.DEPARTEMENTS_INSTITUTS


# Chargement du prompt
with open('prompt.txt', mode='r', encoding='utf-8') as f:
    prompt = ''.join(f.readlines())


# =========================================================================
# PROTECTION DES UNDERSCORES DANS LES FORMULES (nouveau, 05/09/2026)
#
# BUG TROUVÉ (via l'inspection du HTML réellement rendu) : le moteur
# markdown de shinychat interprète le underscore _ D'UNE FORMULE LATEX
# (ex: \hat{b}_{jn}) comme un marqueur d'ITALIQUE markdown (_..._) --
# purement parce qu'il ne sait pas qu'il se trouve à l'intérieur d'une
# zone $ ... $ / $$ ... $$ protégée. Il transforme le premier _ en <em>,
# cherche plus loin un AUTRE _ pour fermer -- et le trouve dans une
# formule complètement différente, fusionnant et détruisant les DEUX
# formules à la fois. Ce n'est PAS un problème de prompt : même une
# formule parfaitement écrite par le modèle casse à cause de ça.
#
# CORRECTIF : on remplace chaque _ trouvé À L'INTÉRIEUR d'une zone maths
# par un jeton inoffensif ("%%US%%") AVANT d'envoyer le texte au chat --
# markdown n'a aucune raison de toucher à ce jeton (ce n'est pas un
# caractère spécial pour lui), donc il traverse le rendu markdown intact.
# Côté client (JS, voir mbfCorrigerLatexManquantHTML), ce jeton est
# reconverti en _ juste avant que KaTeX ne compose la formule -- KaTeX ne
# voit donc plus jamais rien d'autre qu'un vrai underscore, mais
# markdown, lui, ne l'a jamais vu du tout.
# =========================================================================
JETON_UNDERSCORE = "%%US%%"
_MOTIF_ZONE_MATHS = re.compile(r"(\${1,2})(.+?)\1", re.DOTALL)


def _proteger_underscores_maths(texte: str) -> str:
    """Remplace les _ par JETON_UNDERSCORE, uniquement à l'intérieur des
    zones $ ... $ ou $$ ... $$ trouvées dans le texte."""
    def _remplacer(match):
        delimiteur = match.group(1)
        contenu = match.group(2)
        return f"{delimiteur}{contenu.replace('_', JETON_UNDERSCORE)}{delimiteur}"

    return _MOTIF_ZONE_MATHS.sub(_remplacer, texte)


def _tampon_est_equilibre(texte: str) -> bool:
    """True si le texte ne contient AUCUNE zone maths encore "ouverte"
    (un $ ou $$ sans son délimiteur fermant) -- sert à ne jamais couper
    une formule en plein milieu pendant le streaming, ni à appliquer la
    protection sur un texte encore incomplet."""
    nb_doubles = texte.count("$$")
    if nb_doubles % 2 != 0:
        return False
    sans_doubles = texte.replace("$$", "")
    nb_simples = sans_doubles.count("$")
    return nb_simples % 2 == 0


# =========================================================================
# MIGRATION OPENAI -> CLAUDE (21/08/2026) -- génération de texte (chat +
# résumé de document) déplacée vers l'API Anthropic (Claude), via
# ChatAnthropic de chatlas (même librairie, juste un autre "provider" --
# aucun changement nécessaire ailleurs dans le fichier : chat_async(),
# stream_async(), content_image_file() sont génériques dans chatlas, pas
# spécifiques à OpenAI).
#
# EXCEPTION : rag_utils.py continue d'utiliser l'API OpenAI (embeddings
# text-embedding-3-small) pour la recherche RAG -- Anthropic NE PROPOSE
# PAS d'API d'embeddings, ce n'est donc pas remplaçable par Claude. Les
# DEUX clés API restent donc nécessaires dans le fichier .env :
#   OPENAI_API_KEY=...      (embeddings, utilisé par rag_utils.py)
#   ANTHROPIC_API_KEY=...   (chat_client + resume_client ci-dessous)
#
# Modèle choisi : claude-sonnet-5 (meilleure qualité de rédaction ;
# légèrement plus lent/coûteux que claude-haiku-4-5-20251001, qui reste
# une bonne alternative économique pour un déploiement gratuit/démo si
# besoin de réduire les coûts).
# =========================================================================
chat_client = ctl.ChatOpenAI(
    model="gpt-4.1-mini",
    system_prompt=prompt,
)

# NOUVEAU (20/08/2026) : client IA SÉPARÉ pour le résumé des documents
# envoyés (PDF/photo) -- volontairement PAS le même que chat_client
# (dont le prompt est spécifique à la recherche de mentors, hors sujet
# ici). Consignes strictes : uniquement pour de vrais articles
# scientifiques, jamais de résumé détaillé (juste le sujet/titre
# principal), jamais de citation/reproduction de texte -- cohérent avec
# les règles de copyright du projet.
resume_client= ctl.ChatOpenAI(
    model="gpt-4.1-mini",
    system_prompt=(
        "Tu reçois un extrait de document envoyé par un utilisateur de MentorBF "
        "(plateforme d'aide à l'orientation académique au Burkina Faso). "
        "Détermine d'abord s'il s'agit d'un article scientifique (titre, auteurs, "
        "résumé/abstract, structure académique). "
        "Si CE N'EST PAS un article scientifique (photo quelconque, document "
        "administratif, capture d'écran sans rapport...), réponds EXACTEMENT : "
        "\"Ce document ne semble pas être un article scientifique.\" et rien d'autre "
        "(pas de ligne MBF_DOC_SUMMARY ni MBF_SUGGESTIONS dans ce cas). "
        "\n\n"
        "Si c'en est bien un, réponds EXACTEMENT selon cette structure Markdown, "
        "dans cet ordre, sans rien ajouter avant/après/entre :\n"
        "1) D'abord, seule sur sa toute première ligne, exactement : "
        "MBF_DOC_SUMMARY::true\n"
        "2) Puis un titre \"### 📄 Résumé de ton document\"\n"
        "3) Puis une ligne \"**Thème principal :** \" suivie d'UNE SEULE phrase "
        "résumant le sujet général, dans tes propres mots -- jamais un résumé "
        "détaillé du contenu, jamais de citation ni de reproduction de texte, "
        "juste le thème général.\n"
        "4) Puis une ligne \"**Points clés :**\" suivie de 2 à 4 puces Markdown "
        "(\"- \"), chacune TRÈS COURTE (5 à 8 mots maximum) : un angle ou "
        "mot-clé du document (ex: le type d'étude, la méthode générale, le "
        "domaine concerné) -- JAMAIS une explication détaillée des résultats, "
        "de la méthodologie complète ou des conclusions (ça reste réservé aux "
        "questions de suivi ci-dessous, où le contenu réel du document est "
        "fourni). Ce sont des repères visuels courts, pas un résumé développé.\n"
        "5) Enfin, seule sur sa toute dernière ligne, EXACTEMENT sous la forme : "
        "MBF_SUGGESTIONS::[\"Question 1\",\"Question 2\",\"Question 3\"] "
        "contenant 3 questions de suivi pertinentes qu'un étudiant pourrait poser "
        "sur CET article précis (ex: sa méthodologie, ses résultats/conclusions, "
        "son lien avec un domaine d'étude ou un enseignant-chercheur de la base).\n"
        "Les deux lignes MBF_DOC_SUMMARY::true et MBF_SUGGESTIONS::[...] sont des "
        "signaux techniques pour l'application -- elles ne doivent JAMAIS être "
        "annoncées, expliquées ni reformulées dans le texte visible, et ne "
        "doivent apparaître que si c'est bien un article scientifique."
    ),
)


# =========================================================================
# DONNEES MENTORS chargées directement (PAS via l'IA/RAG) : utilisées pour
# la liste consultable par filtres (Université/UFR/Département), qui doit
# être instantanée, gratuite (aucun appel API) et 100% fidèle aux données
# -- contrairement à la recherche en langage naturel du chat, qui passe
# par le RAG + l'IA et peut reformuler/résumer.
# =========================================================================
with open("mentors.json", encoding="utf-8") as f:
    MENTORS = json.load(f)

# Communiqués (événements scientifiques) : fichier séparé, à éditer à la
# main (pas issu du scraping). Voir communiques.json pour le format.
try:
    with open("communiques.json", encoding="utf-8") as f:
        COMMUNIQUES = json.load(f)
except FileNotFoundError:
    COMMUNIQUES = []

# Contenu brut des questions suggérées, chargé ici (avant UFR_GLOBAUX qui
# n'existe pas encore à ce stade du fichier) -- QUESTIONS_SUGGEREES_APLATIES
# (la liste utile, dépendante de UFR_GLOBAUX) est calculée juste après la
# définition de UFR_GLOBAUX, plus bas dans ce fichier.
try:
    with open("questions_suggerees.json", encoding="utf-8") as f:
        QUESTIONS_SUGGEREES_PAR_UFR = json.load(f)
except FileNotFoundError:
    QUESTIONS_SUGGEREES_PAR_UFR = {}


def _ufr_correspond(mentor_ufr_texte: str, ufr_label: str) -> bool:
    """Vérifie si le champ 'ufr' scrapé d'un mentor correspond au libellé
    d'UFR/Institut choisi dans la sidebar (ex: 'UFR/SEA - Sciences Exactes
    et Appliquées' doit matcher le texte scrapé 'UFR Sciences Exactes et
    Appliquées'). Comparaison floue par recouvrement de mots significatifs
    (>3 lettres, en ignorant les mots trop génériques comme 'Sciences' qui
    reviennent dans presque tous les libellés d'UFR -- bug corrigé : sans
    ce filtre, 'Sciences de la Vie et de la Terre' et 'Sciences Exactes et
    Appliquées' matchaient à tort à cause du seul mot commun 'sciences')."""
    if not mentor_ufr_texte or mentor_ufr_texte == "Information non disponible":
        return False
    if not ufr_label or ufr_label == "Toutes les UFR":
        return True

    code, _, description = ufr_label.partition(" - ")
    sigle = code.split("/")[-1].strip().lower()
    mentor_lower = mentor_ufr_texte.lower()

    if sigle and len(sigle) > 2 and sigle in mentor_lower:
        return True

    mots_generiques = {"sciences", "science", "faculte", "unite", "recherche", "ufr", "universite"}
    mots_label = {w for w in re.findall(r"\w+", description.lower()) if len(w) > 3 and w not in mots_generiques}
    mots_mentor = {w for w in re.findall(r"\w+", mentor_lower) if len(w) > 3}
    if not mots_label:
        return False
    recouvrement = mots_label & mots_mentor
    return len(recouvrement) / len(mots_label) >= 0.5


def _filieres_connues(code_univ: str, ufr_label: str) -> list:
    """Renvoie les filières (= départements) OFFICIELLEMENT connues pour
    cette université + UFR/institut, directement depuis la structure de
    référence (ORGANIGRAMME/DEPARTEMENTS_INSTITUTS) -- PAS depuis
    mentors.json. Une filière apparaît ici même si aucun enseignant n'y
    est encore rattaché avec succès, cohérent avec le comportement
    d'Université/UFR."""
    if not code_univ:
        return []

    sigle_ufr = ufr_label.split(" - ")[0].strip() if ufr_label and ufr_label != "Toutes les UFR" else None

    filieres = set()
    organigramme_univ = ORGANIGRAMME.get(code_univ, {})
    instituts_univ = DEPARTEMENTS_INSTITUTS.get(code_univ, {})

    if sigle_ufr:
        for departement, _ in organigramme_univ.get(sigle_ufr, []):
            filieres.add(departement)
        for departement, _ in instituts_univ.get(sigle_ufr, []):
            filieres.add(departement)
    else:
        # Aucune UFR précise choisie ("Toutes les UFR") : union de toutes
        # les filières connues de cette université, tous UFR/instituts
        # confondus.
        for departements in organigramme_univ.values():
            filieres.update(d for d, _ in departements)
        for departements in instituts_univ.values():
            filieres.update(d for d, _ in departements)

    return sorted(filieres)


def departements_disponibles(univ_label: str, ufr_label: str) -> list:
    """Liste des départements/filières OFFICIELLEMENT connus pour la
    combinaison Université + UFR/Institut sélectionnée, depuis la
    structure de référence (20_Extraire_departement.py), pas depuis les
    données réelles des enseignants. Voir _filieres_connues ci-dessus."""
    code_univ = _code_universite(univ_label)
    return _filieres_connues(code_univ, ufr_label)


def specialites_disponibles(univ_label: str, ufr_label: str, departement_label: str) -> list:
    """Spécialités précises disponibles pour la combinaison Université +
    UFR + Département sélectionnée (ex: département 'Mathématiques' ->
    'Algèbre', 'Mathématiques appliquées'...)."""
    if not departement_label or departement_label in ("Tous les départements", "à compléter"):
        return []
    code_univ = _code_universite(univ_label)
    candidats = MENTORS if code_univ is None else [
        m for m in MENTORS if m.get("universite_code") == code_univ
    ]
    if ufr_label and ufr_label != "Toutes les UFR":
        candidats = [m for m in candidats if _ufr_correspond(m.get("ufr", ""), ufr_label)]
    candidats = [m for m in candidats if m.get("departement") == departement_label]

    valeurs = {
        m.get("specialite") for m in candidats
        if m.get("specialite") and m.get("specialite") != "Information non disponible"
    }
    return sorted(valeurs)


def filtrer_mentors(univ_label: str, ufr_label: str, departement: str, specialite: str) -> list:
    """Filtre direct sur mentors.json (aucun appel IA) pour la liste
    consultable. Renvoie une liste de dicts simplifiés prêts à afficher."""
    code_univ = _code_universite(univ_label)
    resultats = MENTORS
    if code_univ is not None:
        resultats = [m for m in resultats if m.get("universite_code") == code_univ]
    if ufr_label and ufr_label != "Toutes les UFR":
        resultats = [m for m in resultats if _ufr_correspond(m.get("ufr", ""), ufr_label)]
    if departement and departement not in ("Tous les départements", "à compléter"):
        resultats = [m for m in resultats if m.get("departement") == departement]
    if specialite and specialite != "Toutes les spécialités":
        resultats = [m for m in resultats if m.get("specialite") == specialite]
    return sorted(resultats, key=lambda m: m.get("name", ""))


def _bloc_questions_suggerees(sigle_choisi: str):
    """UI des questions suggérées pour l'UFR CHOISI dans le menu déroulant
    ci-dessus (même principe de cascade que Université -> UFR dans les
    filtres du haut : rien ne s'affiche tant que l'étudiant n'a pas choisi
    un UFR -- plus compact qu'un accordéon qui liste tout d'un coup).

    Les boutons gardent leurs id STATIQUES (question_suggeree_0, _1, ...,
    fixés une fois pour toutes par QUESTIONS_SUGGEREES_APLATIES au
    démarrage de l'appli, voir plus bas la boucle d'enregistrement des
    handlers) -- seul ce qui est AFFICHÉ ici change selon l'UFR choisi,
    jamais les id ni les handlers eux-mêmes."""
    if not sigle_choisi or sigle_choisi == "Choisir un UFR":
        return ui.p(
            "Choisis un UFR ci-dessus pour voir des questions suggérées.",
            style="font-size: 0.85em; color: #777;",
        )

    boutons = [
        ui.input_action_button(
            f"question_suggeree_{i}",
            f"💬 {question}",
            class_="btn-sm w-100",
            style="margin-bottom: 4px; white-space: normal; text-align: left; font-size: 0.82em;",
        )
        for i, (sigle, question) in enumerate(QUESTIONS_SUGGEREES_APLATIES)
        if sigle == sigle_choisi
    ]
    if not boutons:
        return ui.p(
            "Pas encore de questions suggérées pour cet UFR -- à compléter.",
            style="font-size: 0.85em; color: #777;",
        )
    return ui.TagList(*boutons)


def _bloc_communiques():
    """UI des communiqués (événements scientifiques), lus depuis
    communiques.json -- fichier à éditer à la main, voir son format
    d'exemple. Affiche les 5 plus récents (triés par date décroissante)."""
    if not COMMUNIQUES:
        return [ui.p("Aucun communiqué pour l'instant.", style="font-size: 0.85em; color: #777;")]

    events_tries = sorted(COMMUNIQUES, key=lambda c: c.get("date", ""), reverse=True)
    blocs = []
    for c in events_tries[:5]:
        contenu = [
            ui.p(ui.strong(c.get("titre", "Sans titre")), style="margin-bottom: 2px;"),
            ui.p(c.get("date", ""), style="font-size: 0.8em; color: #888; margin-bottom: 2px;"),
            ui.p(c.get("description", ""), style="font-size: 0.85em; margin-bottom: 2px;"),
        ]
        if c.get("lien"):
            contenu.append(
                ui.tags.a("En savoir plus →", href=c["lien"], target="_blank", style="font-size: 0.85em;")
            )
        blocs.append(ui.div(*contenu, style="margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid #eee;"))
    return blocs


UFR_PAR_UNIVERSITE = {
    # =====================================================================
    # Structure vérifiée le 20/08/2026 par scraping automatisé
    # d'oreilleducampus.org (script scraper_oreilleducampus.py) +
    # recoupement avec le répertoire officiel MESRSI et les cartographies
    # manuelles. Voir cartographie_universites_ufr_filieres.md pour le
    # détail complet des filières par UFR/institut et les notes de
    # confiance (✅ confirmé / ⚠️ à vérifier / 🆕 nouveau) -- ce fichier-ci
    # ne garde que les UFR/instituts eux-mêmes (pas le détail des
    # filières), pour peupler les menus déroulants de la sidebar.
    # =====================================================================
    "UJKZ - Université Joseph KI-ZERBO": [
        "UFR/SEA - Sciences Exactes et Appliquées",
        "UFR/SVT - Sciences de la Vie et de la Terre",
        "UFR/SH - Sciences Humaines",
        "UFR/LAC - Lettres, Arts et Communication",
        "UFR/SDS - Sciences de la Santé",
        "ISSP - Institut Supérieur des Sciences de la Population",
        "IBAM - Institut Burkinabè des Arts et Métiers",
        "IFOAD - Institut de Formation Ouverte et à Distance",
        "IGEDD - Institut du Génie de l'Environnement et du Développement Durable",
        "IPERMIC - Institut Panafricain d'Études et de Recherche sur les Médias, l'Information et la Communication",
        "ISSDH - Institut Supérieur des Sciences du Sport et du Développement Humain",
        # Ajoutées le 20/08/2026 : campus décentralisés de l'UJKZ (les
        # enseignants y sont tagués universite_code="UJKZ" dans
        # mentors.json, PAS "CUP/K"/"CUP/Z" -- voir diagnostic_departements.py
        # et 20_Extraire_departement.py, DEPARTEMENTS_INSTITUTS["UJKZ"]).
        # Le sigle DOIT rester exactement "CU Kaya"/"CU Ziniare" (sans
        # accent), identique à celui utilisé dans ce dict.
        "CU Kaya - Centre Universitaire de Kaya (campus décentralisé UJKZ)",
        "CU Ziniare - Centre Universitaire de Ziniaré (campus décentralisé UJKZ)",
    ],
    "UNB - Université Nazi BONI": [
        # Structure ENTIÈREMENT REVUE le 20/08/2026 (2ᵉ correction) --
        # confirmée cette fois directement sur le site officiel
        # univ-bobo.gov.bf (menu "Etablissements" + fiches individuelles
        # consultées une par une), qui s'est avéré PLUS FIABLE que
        # oreilleducampus.org pour cette université (dont la structure
        # UFR/ST-SJP-SEG+ESI+INSSA+IUT ne correspond en réalité à AUCUN
        # enseignant réel dans mentors.json -- voir diagnostic_sigles_ufr.py).
        "UFR/SEA - Sciences Exactes et Appliquées",
        "UFR/SVT - Sciences de la Vie et de la Terre",
        "UFR/SH-LAM - Sciences Humaines, Lettres, Arts et Média",
        "UFR/SJPEG - Sciences Juridiques, Politiques, Économiques et de Gestion",
        "ESI - École Supérieure d'Informatique",
        "INSSA - Institut Supérieur des Sciences de la Santé",
        "IUT - Institut Universitaire de Technologie",
        "CUB - Centre Universitaire de Banfora",
        "CUG - Centre Universitaire de Gaoua",
        "IDR - Institut du Développement Rural",
        "IC - Institut Confucius",
    ],
    "UNZ - Université Norbert ZONGO": [
        "UFR/ST - Sciences et Technologies",
        "UFR/SEG - Sciences Économiques et de Gestion",
        "UFR/LSH - Lettres et Sciences Humaines",
        "IUT - Institut Universitaire de Technologie",
        # Nouveau : rattachée à l'UNZ sur oreilleducampus.org, mais listée
        # comme établissement INDÉPENDANT dans le répertoire officiel
        # MESRSI -- à trancher si on la traite comme faisant partie de
        # l'UNZ ou comme un universite_code séparé.
        "ENS - École Normale Supérieure de Koudougou",
        # Ajouté le 20/08/2026 : confirmé structure officielle de l'UNZ
        # (arrêté n°2018-343/MESRSI/CAB/SG du 12/06/2018) -- même situation
        # que Kaya/Ziniaré pour l'UJKZ.
        "CU Manga - Centre Universitaire de Manga (campus décentralisé UNZ)",
    ],
    "UTS - Université Thomas SANKARA": [
        "UFR/SEG - Sciences Économiques et de Gestion",
        "UFR/SJP - Sciences Juridiques et Politiques",
        "UFR/ST - Sciences et Techniques",
        "IUFIC - Institut Universitaire de Formations Initiale et Continue",
        "IFOAD - Institut de Formation Ouverte et à Distance",
    ],
    "ULBO - Université Lédéa Bernard OUEDRAOGO": [
        # Structure CORRIGÉE le 20/08/2026 (2ᵉ correction) : legrandfrere.africa
        # a expliqué la réorganisation de 2018-2019 -- les 4 anciennes écoles
        # (ESSAN, ESCME, ESJPA, ESST, 2010-2018) ont été remplacées par 3
        # établissements actuels. ESJPA a été RETIRÉE d'ici : elle est
        # dissoute, ses formations (GRH, GFI) ont été absorbées par ESCME.
        "ESSAN - École Supérieure des Sciences de la Santé",
        "ESCME - École Supérieure de Commerce, de Management et d'Entreprenariat (a absorbé les formations de l'ex-ESJPA : GRH, GFI)",
        "ESST - École Supérieure des Sciences et Technologies",
    ],
    "UYAT - Université Yembila Abdoulaye TOGUYENI": [
        # Structure ENTIÈREMENT REMPLACÉE le 20/08/2026 (3ᵉ correction) :
        # les 4 UFR précédentes (UFR/SAT, IUP, UFR/LSH, UFR/SEG) venaient
        # d'une cartographie jamais re-confirmée -- remplacées par la
        # vraie structure, confirmée par 2 sources concordantes (AUF +
        # article MESRSI 2020) : l'université compte des établissements
        # organisés par département (pas par "filière" comme ailleurs).
        "ENSI-F - École Nationale Supérieure d'Ingénieurs de Fada N'Gourma (aussi appelée ESI - École Supérieure d'Ingénierie)",
        "ISDD - Institut Supérieur de Développement Durable",
        "UFR/SHLAC - Sciences Humaines, Lettres, Arts et Communication",
        # I3S : mentionné par legrandfrere.africa comme 4ᵉ établissement
        # (santé), mais sans détail de départements pour l'instant --
        # conservé quand même dans la sidebar, département "à compléter"
        # en attendant confirmation.
        "I3S - Institut Supérieur des Sciences de la Santé",
    ],
    "UDOC - Université Daniel OUEZZIN COULIBALY": [
        # ✅ Confirmée à l'identique par scraping automatisé (2 passages)
        # -- aucune modification nécessaire.
        "UFR/SAT - Sciences Appliquées et Technologies",
        "UFR/LSH - Lettres et Sciences Humaines",
        "ISEDR - Institut Supérieur d'Études et de Développement Rural",
        "IUP - Institut Universitaire Professionnalisé",
    ],
    "UV-BF - Université Virtuelle du Burkina Faso": [
        # Structure CORRIGÉE le 20/08/2026 (2ᵉ correction) : confirmée par
        # capture directe de la brochure officielle UV-BF -- plus riche
        # que ce qu'on avait (Sciences Transversales inclut aussi Lettres
        # Modernes, Économie, Droit ; niveau Master avec Cybersécurité,
        # Santé Numérique, etc.). La structure "Programme Sciences
        # fondamentales/transversales/numérique" qu'on avait mise ici par
        # erreur appartient en fait à l'EPO (les deux établissements
        # utilisent la même terminologie de "Programme", d'où la
        # confusion initiale).
        "Sciences Fondamentales (Licence)",
        "Sciences Transversales (Licence)",
        "Sciences du Numérique (Licence)",
    ],
    "EPO - École Polytechnique de Ouagadougou": [
        # Structure RENSEIGNÉE le 20/08/2026 : la table "Programme Sciences
        # fondamentales/transversales/numérique" qu'on avait attribuée par
        # erreur à l'UV-BF appartient en fait à l'EPO (confirmé par
        # capture manuelle directe).
        "Programme Sciences fondamentales",
        "Programme Sciences transversales",
        "Programme Sciences du numérique",
    ],
    # =====================================================================
    # CENTRES UNIVERSITAIRES POLYTECHNIQUES (CUP) -- ajoutés le 20/08/2026
    # suite à la décision de couvrir aussi les centres, pas seulement les
    # universités publiques "historiques" (les étudiants y sont orientés
    # au même titre). Chaque CUP est actuellement non subdivisé en UFR
    # distinctes sur les sources disponibles -- une seule entrée par CUP.
    #
    # ⚠️ ATTENTION -- SUSPICION DE DOUBLON AVEC UJKZ (à vérifier) :
    # on a découvert que Kaya et Ziniaré, malgré leur code "CUP/K"/"CUP/Z"
    # ici, correspondent en réalité à des CAMPUS DÉCENTRALISÉS de l'UJKZ
    # dans les vraies données (universite_code="UJKZ" dans mentors.json,
    # voir diagnostic_departements.py) -- ils ont donc été retirés d'ici
    # et rajoutés comme UFR/institut sous "UJKZ - Université Joseph
    # KI-ZERBO" plus haut, avec leurs vrais départements. LES 5 AUTRES CUP
    # CI-DESSOUS (Banfora, Gaoua, Dori, Tenkodogo, Manga) N'ONT PAS ENCORE
    # ÉTÉ VÉRIFIÉS de la même façon -- ils pourraient être eux aussi des
    # campus décentralisés d'une université existante plutôt que des
    # établissements réellement indépendants. À confirmer avec de
    # nouvelles captures avant de faire confiance à ces entrées "CUP/..."
    # séparées : si mentors.json ne contient jamais universite_code=
    # "CUP/B" (ou G/D/T/M), c'est le signe qu'il faut les rattacher à
    # leur université mère, comme pour Kaya/Ziniaré.
    # =====================================================================
    "CUP/B - Centre Universitaire Polytechnique de Banfora": [
        "CUP/B - Centre Universitaire Polytechnique de Banfora (non subdivisé)",
    ],
    "CUP/G - Centre Universitaire Polytechnique de Gaoua": [
        "CUP/G - Centre Universitaire Polytechnique de Gaoua (non subdivisé)",
    ],
    "CUP/D - Centre Universitaire Polytechnique de Dori": [
        "CUP/D - Centre Universitaire Polytechnique de Dori (non subdivisé)",
    ],
    "CUP/T - Centre Universitaire Polytechnique de Tenkodogo": [
        "CUP/T - Centre Universitaire Polytechnique de Tenkodogo (non subdivisé)",
    ],
    # CUP/M (Manga) RETIRÉ d'ici le 20/08/2026 : confirmé comme structure
    # de l'Université Norbert ZONGO (arrêté n°2018-343/MESRSI/CAB/SG),
    # exactement comme Kaya/Ziniaré pour l'UJKZ -- déplacé sous
    # "UNZ - Université Norbert ZONGO", voir plus haut.
    # CUP/K (Kaya) et CUP/Z (Ziniaré) RETIRÉS d'ici le 20/08/2026 : ce
    # sont en réalité des campus décentralisés de l'UJKZ (voir warning
    # ci-dessus) -- déplacés sous "UJKZ - Université Joseph KI-ZERBO"
    # comme UFR/institut ("CU Kaya", "CU Ziniare"), seul endroit où ils
    # correspondent vraiment aux données de mentors.json.
}

# Listes dérivées de l'organigramme (20/08/2026) -- pour le champ
# "Université"/"UFR" du profil "Enseignant" : proposer les vraies
# universités/UFR connues, tout en laissant la personne saisir librement
# si son établissement n'y figure pas (voir ui.input_selectize avec
# options={"create": True} plus bas).
UNIVERSITES_TOUTES = list(UFR_PAR_UNIVERSITE.keys())
UFR_TOUTES = sorted({
    ufr for liste_ufr in UFR_PAR_UNIVERSITE.values() for ufr in liste_ufr
})


def _sigle_ufr(label_complet: str) -> str:
    """'UFR/SVT - Sciences de la Vie et de la Terre' -> 'UFR/SVT' (même
    principe que _code_universite plus bas)."""
    return label_complet.split(" - ")[0].strip()


# Liste globale des UFR/Instituts, DEDOUBLONNEE par sigle : comme la
# structure est presque la même d'une université à l'autre (ex: UFR/SEA
# existe à l'UJKZ ET à l'UNB avec le même intitulé), on ne veut PAS
# répéter les mêmes questions suggérées une fois par université -- juste
# une fois par sigle. On garde le premier libellé complet rencontré pour
# l'affichage (si un sigle a un libellé légèrement différent selon
# l'université -- ex: UFR/ST "Technologies" à l'UNZ vs "Techniques" à
# l'UTS -- ce n'est qu'un affichage, jamais utilisé pour filtrer).
UFR_GLOBAUX = {}
for _liste_ufr in UFR_PAR_UNIVERSITE.values():
    for _label in _liste_ufr:
        _sigle = _sigle_ufr(_label)
        if _sigle not in UFR_GLOBAUX and _label not in ("Structure à compléter", "Programmes (structure non classique)"):
            UFR_GLOBAUX[_sigle] = _label

# Liste APLATIE (sigle, question), dans l'ordre de UFR_GLOBAUX -- calculée
# une seule fois au démarrage, maintenant que UFR_GLOBAUX existe. C'est
# elle qui fixe les id statiques des boutons (question_suggeree_0, _1...)
# et donc l'enregistrement des handlers plus bas (voir
# _faire_handler_question_suggeree). Seuls les UFR qui ont VRAIMENT des
# questions dans le JSON génèrent un panneau d'accordéon -- pas la peine
# d'afficher un panneau vide.
QUESTIONS_SUGGEREES_APLATIES = [
    (sigle, question)
    for sigle in UFR_GLOBAUX
    for question in QUESTIONS_SUGGEREES_PAR_UFR.get(sigle, [])
]

MESSAGE_ACCUEIL = (
    "Bonjour ! Je suis MentorBF, un chatbot qui centralise les "
    "enseignants-chercheurs burkinabè -- leurs domaines d'expertise, "
    "leurs publications, leurs travaux de recherche."
)

# NOUVEAU (22/08/2026) : phrase pratique retirée de MESSAGE_ACCUEIL (pour
# alléger l'écran d'accueil) et déplacée en petite explication sous
# "Filtrer par" dans la sidebar -- voir sidebar_contenu().
EXPLICATION_FILTRES = "Filtre par université, UFR, département, puis lance ta recherche."


# Titre et mise en page
ui.page_opts(
    title="MentorBF",
    fillable=True,
    full_width=True,
)

# ---- Couleurs MentorBF (vert + doré) ----
ui.tags.style("""
    :root {
        --mbf-vert: #1a5c3f;
        --mbf-or: #f5b301;
    }
    .navbar, .bslib-page-title, header.navbar {
        background-color: var(--mbf-vert) !important;
    }
    .navbar .navbar-brand, .navbar-brand {
        color: white !important;
        font-weight: 600;
    }
    .btn-primary, #rechercher, #btn_login, #btn_signup {
        background-color: var(--mbf-or) !important;
        border-color: var(--mbf-or) !important;
        color: var(--mbf-vert) !important;
        font-weight: 600 !important;
    }
    .btn-primary:hover, #rechercher:hover {
        filter: brightness(0.95);
    }
    .sidebar, .bslib-sidebar-layout > .sidebar {
        background-color: #ffffff !important;
    }
    /* NOUVEAU (20/08/2026) : petit indicateur de connexion épinglé en
       haut à droite de l'interface, peu importe où il est réellement
       placé dans le HTML (voir zone_connexion_topright()). Jamais le nom
       affiché -- juste "connecté" ou "se connecter". */
    #mbf_topright {
        position: fixed;
        top: 10px;
        right: 16px;
        z-index: 1050;
    }
    #mbf_topright .btn {
        font-size: 0.85em;
        padding: 4px 12px;
        border-radius: 20px;
    }
    /* NOUVEAU (22/08/2026 -- v4) : "+ Nouveau" façon claude.ai, en haut
       de la sidebar, au-dessus de "Filtrer par" (voir sidebar_contenu()).
       Bouton pleine largeur, fond gris clair, texte à gauche -- plus une
       icône flottante près du chat (les tentatives précédentes de
       positionnement précis n'étaient pas fiables). */
    #nouvelle_conversation {
        display: flex;
        align-items: center;
        gap: 8px;
        width: 100%;
        text-align: left;
        background-color: #f0f0f0;
        border: none;
        border-radius: 8px;
        padding: 10px 12px;
        font-weight: 600;
        color: #1a1a1a;
        margin-bottom: 14px;
    }
    #nouvelle_conversation:hover {
        background-color: #e4e4e4;
    }
    /* Bouton "Continuer avec Google" -- style proche du bouton officiel
       Google Sign-In (blanc, bordure grise, logo à gauche, texte gris
       foncé) -- VRAI lien <a>, pas un bouton Shiny, pour forcer une
       vraie navigation de page vers Google (voir _construire_modal_etape1). */
    #mbf_btn_google {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        padding: 10px 16px;
        border: 1px solid #dadce0;
        border-radius: 24px;
        background-color: #fff;
        color: #3c4043;
        font-weight: 600;
        font-size: 0.92rem;
        text-decoration: none;
        box-shadow: 0 1px 2px rgba(0,0,0,0.08);
        transition: box-shadow 0.15s ease, background-color 0.15s ease;
    }
    #mbf_btn_google:hover {
        background-color: #f8f9fa;
        box-shadow: 0 1px 4px rgba(0,0,0,0.15);
        color: #3c4043;
    }
    .mbf-btn-google-accueil {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
        padding: 10px 16px;
        border: 1px solid #dadce0;
        border-radius: 24px;
        background-color: #fff;
        color: #3c4043;
        font-weight: 600;
        font-size: 0.92rem;
        text-decoration: none;
        box-shadow: 0 1px 2px rgba(0,0,0,0.08);
        transition: box-shadow 0.15s ease, background-color 0.15s ease;
    }
    .mbf-btn-google-accueil:hover {
        background-color: #f8f9fa;
        box-shadow: 0 1px 4px rgba(0,0,0,0.15);
        color: #3c4043;
    }
    /* Formulaire de connexion en 2 volets face-à-face */
    .mbf-auth-colonnes {
        display: flex;
        gap: 24px;
        flex-wrap: wrap;
    }
    .mbf-auth-colonnes > div {
        flex: 1 1 260px;
    }
    @media (max-width: 600px) {
        .mbf-auth-colonnes { flex-direction: column; }
    }
    /* NOUVEAU : "Se connecter" / "Créer un compte" en noir, même couleur
       que le texte du bouton Google (#3c4043) -- cohérence visuelle
       entre les deux options de connexion. */
    .mbf-auth-titre {
        color: #3c4043;
        font-weight: 700;
    }
    /* Caractères plus discrets dans les modales de connexion/profil --
       le titre de la modale bslib et les labels des champs étaient trop
       gros par défaut. */
    #shiny-modal .modal-title {
        font-size: 1.25rem !important;
    }
    #shiny-modal h5 {
        font-size: 1rem !important;
        font-weight: 600;
    }
    #shiny-modal label {
        font-size: 0.85rem !important;
    }
    #shiny-modal .form-control, #shiny-modal .selectize-input {
        font-size: 0.9rem !important;
    }
    #shiny-modal p {
        font-size: 0.85rem !important;
    }
    /* Envoi de PDF/photo : repositionné SOUS la zone de saisie du
       chat (et non plus au-dessus), en couleur pour être visible,
       "No file selected" retiré. On réserve un peu d'espace vide sous
       la barre de saisie du chat (shiny-chat-container) pour que le
       bouton ait de la place sans jamais chevaucher la saisie. */
    shiny-chat-container {
        padding-bottom: 46px;
    }
    /* Centré dans la VRAIE zone de chat (après les 300px de la sidebar),
       pas sur tout le viewport -- même correction que pour #mbf_hero_accueil
       plus haut (sinon le bouton apparaît décalé vers la gauche). Repli
       sans décalage sous 767px, où la sidebar passe en superposition. */
    #mbf_upload_zone {
        position: fixed;
        bottom: 20px;
        left: calc(50% + 150px);
        transform: translateX(-50%);
        z-index: 1040;
    }
    @media (max-width: 767px) {
        #mbf_upload_zone {
            left: 50%;
        }
    }
    #mbf_upload_zone .form-group {
        margin-bottom: 0;
    }
    #mbf_upload_zone .progress {
        display: none;
    }
    /* Le composant input_file de Shiny ajoute par défaut un champ texte
       en lecture seule (vide) à côté du bouton -- c'est la partie
       "divisée vide" visible sur la capture. On la masque et on force
       le groupe à ne prendre que la largeur du bouton. */
    #mbf_upload_zone .input-group {
        display: inline-flex !important;
        width: auto !important;
    }
    #mbf_upload_zone .input-group input[type="text"].form-control {
        display: none !important;
    }
    #mbf_upload_zone .btn-file {
        background-color: var(--mbf-or) !important;
        color: var(--mbf-vert) !important;
        font-weight: 600;
        border: none;
        border-radius: 20px;
        padding: 6px 14px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.18);
        font-size: 0.85rem;
    }
    #mbf_upload_zone .btn-file:hover {
        filter: brightness(0.95);
    }

    /* DÉSACTIVE le trait bleu par défaut de Shiny en haut de la page
       (nouveau, 22/08/2026) -- trop discret pour servir de vrai retour
       visuel, et donnait l'impression que "ça tourne" une première fois
       avant même que notre propre overlay (#mbf_overlay_busy ci-dessous)
       n'apparaisse. On a notre propre indicateur explicite, celui-ci
       devient redondant. */
    html.shiny-busy::after,
    body.shiny-busy::after {
        display: none !important;
    }

    /* BOUTON "STOP" DE LA ZONE DE SAISIE DU CHAT (22/08/2026) -- déjà
       natif dans shinychat (remplace automatiquement la flèche d'envoi
       pendant qu'une réponse arrive, comme sur claude.ai) -- ici on
       renforce juste sa visibilité : petit fond rouge clair en cercle
       autour de l'icône, pour que ce soit un vrai bouton reconnaissable
       au premier coup d'œil, pas juste une icône qui change de couleur. */
    .shiny-chat-input .shiny-chat-btn-send.shiny-chat-btn-cancel {
        background-color: rgba(220, 53, 69, 0.12) !important;
        border-radius: 50% !important;
    }
    .shiny-chat-input .shiny-chat-btn-send.shiny-chat-btn-cancel:hover {
        background-color: rgba(220, 53, 69, 0.22) !important;
    }

    /* OVERLAY DE CHARGEMENT (nouveau, 21/08/2026) -- spinner bien
       visible, centré, pour l'analyse de document et les recherches
       (sidebar + bouton "Rechercher"), voir _busy_afficher() côté Python. */
    #mbf_overlay_busy {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 2000;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 14px;
        background: rgba(255, 255, 255, 0.72);
        backdrop-filter: blur(1px);
        pointer-events: none;
    }
    .mbf-spinner {
        width: 46px;
        height: 46px;
        border-radius: 50%;
        border: 5px solid rgba(26, 92, 63, 0.15);
        border-top-color: var(--mbf-vert);
        animation: mbf-spin 0.8s linear infinite;
    }
    @keyframes mbf-spin {
        to { transform: rotate(360deg); }
    }
    .mbf-spinner-texte {
        font-weight: 600;
        color: var(--mbf-vert);
        background: #fff;
        padding: 6px 14px;
        border-radius: 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.12);
        font-size: 0.9rem;
    }

    /* HISTORIQUE (22/08/2026 -- v2) -- select compact (comme Université/
       UFR) + 2 petits boutons "▶ Lancer" / "🗑 Supprimer" pour l'entrée
       sélectionnée. Passe mieux à l'échelle qu'une liste dépliable si
       l'utilisateur a beaucoup de recherches enregistrées. */
    .mbf-hist-actions {
        display: flex;
        gap: 6px;
        margin-top: 6px;
    }
    .mbf-hist-btn {
        flex: 1;
        border: none;
        border-radius: 6px;
        font-size: 0.78rem;
        padding: 6px 4px;
        cursor: pointer;
        font-weight: 600;
    }
    .mbf-hist-btn-lancer {
        background: rgba(26, 92, 63, 0.1);
        color: var(--mbf-vert);
    }
    .mbf-hist-btn-lancer:hover {
        background: var(--mbf-vert);
        color: #fff;
    }
    .mbf-hist-btn-supprimer {
        background: rgba(176, 0, 32, 0.08);
        color: #b00020;
    }
    .mbf-hist-btn-supprimer:hover {
        background: #b00020;
        color: #fff;
    }

    /* ÉCRAN D'ACCUEIL (nouveau, 22/08/2026) -- logo + titre + texte,
       affiché uniquement tant qu'aucun message n'a été envoyé (voir JS
       mbfSetup). Design moderne et sobre : logo circulaire, titre net,
       sous-texte discret sous le logo -- pas une bulle de chat.

       BUG CORRIGÉ (centrage) : "left: 0" centrait ce bloc par rapport à
       TOUT le viewport, sidebar comprise (ui.sidebar(width=300) plus
       bas) -- résultat : visuellement décalé/coupé à gauche dans la
       zone de chat réelle (qui commence après les 300px de la
       sidebar). "left: 300px" centre désormais le bloc dans la vraie
       zone visible, avec un repli à 0 sur petit écran (sidebar qui
       passe en superposition/repliée sous ce seuil). */
    #mbf_hero_accueil {
        position: fixed;
        top: 0;
        left: 300px;
        right: 0;
        bottom: 84px;
        z-index: 5;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 14px;
        padding: 24px;
        text-align: center;
        pointer-events: none;
        opacity: 1;
        transition: opacity 0.35s ease;
    }
    @media (max-width: 767px) {
        #mbf_hero_accueil {
            left: 0;
        }
    }
    #mbf_hero_accueil.mbf-hero-hidden {
        opacity: 0;
        pointer-events: none;
        visibility: hidden;
    }
    #mbf_hero_logo {
        width: 108px;
        height: 108px;
        border-radius: 50%;
        object-fit: cover;
        box-shadow: 0 4px 18px rgba(26, 92, 63, 0.25);
        border: 3px solid #fff;
        outline: 1px solid rgba(26, 92, 63, 0.12);
    }
    #mbf_hero_titre {
        font-size: 1.4rem;
        font-weight: 700;
        color: var(--mbf-vert);
        letter-spacing: 0.02em;
    }
    #mbf_hero_texte {
        max-width: 480px;
        font-size: 1.05rem;
        line-height: 1.6;
        color: #1a1a1a;
        font-weight: 500;
    }

    /* MESSAGE "RÉSUMÉ DE DOCUMENT" DANS LE CHAT (nouveau, 21/08/2026) --
       coloration distincte pour ne pas le confondre avec une réponse
       normale du chatbot. Classe ajoutée par le JS (mbfSetup, voir plus
       bas) quand le message contient le marqueur MBF_DOC_SUMMARY::true
       (masqué, comme MBF_SUGGESTIONS::[...]) produit par resume_client.
       Taille de texte réduite (pas besoin d'un gros résumé) et UNE SEULE
       icône (l'emoji 📄 dans le titre du message -- l'avatar rond par
       défaut du chat n'est pas surchargé, pour éviter le doublon). */
    shiny-chat-message.mbf-doc-summary-message {
        background: linear-gradient(135deg, rgba(245, 179, 1, 0.10), rgba(26, 92, 63, 0.05));
        border-left: 4px solid var(--mbf-or);
        border-radius: 10px;
        padding: 12px 14px 12px 12px;
        font-size: 0.85rem;
    }
    shiny-chat-message.mbf-doc-summary-message h3 {
        font-size: 0.95rem;
        margin-bottom: 6px;
    }
    shiny-chat-message.mbf-doc-summary-message ul {
        margin-bottom: 0;
        padding-left: 1.1rem;
    }

    /* ÉCRAN DE CONNEXION (refonte moderne, 22/08/2026) -- logo qui se
       révèle depuis le flou, titre net en noir, texte façon machine à
       écrire (voir JS lancerMachineAEcrire() dans mbfSetup). */
    #mbf_login_logo {
        width: 110px;
        height: 110px;
        border-radius: 50%;
        object-fit: cover;
        box-shadow: 0 4px 18px rgba(26, 92, 63, 0.25);
        border: 3px solid #fff;
        outline: 1px solid rgba(26, 92, 63, 0.12);
        margin-bottom: 6px;
        filter: blur(10px);
        opacity: 0;
        animation: mbf-logo-reveal 1.6s ease-out forwards;
    }
    @keyframes mbf-logo-reveal {
        0%   { filter: blur(10px); opacity: 0; transform: scale(0.94); }
        100% { filter: blur(0);    opacity: 1; transform: scale(1); }
    }
    #mbf_login_titre {
        font-size: 1.7rem;
        font-weight: 700;
        color: #1a1a1a;
        margin-bottom: 4px;
    }
    #mbf_login_texte {
        color: #333;
        font-size: 1rem;
        line-height: 1.55;
        min-height: 4.6em; /* réserve la place avant que le texte ne se tape */
    }
    #mbf_login_texte.mbf-typing::after {
        content: "";
        display: inline-block;
        width: 2px;
        height: 1em;
        margin-left: 2px;
        background: var(--mbf-vert);
        vertical-align: text-bottom;
        animation: mbf-caret-blink 0.8s step-end infinite;
    }
    @keyframes mbf-caret-blink {
        50% { opacity: 0; }
    }

    #auth_panel {
        max-width: 420px;
        margin: 3rem auto;
        padding: 2rem;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
    }
    /* Sidebar plus compacte : les filtres étaient trop espacés */
    .sidebar .form-group, .sidebar .shiny-input-container {
        margin-bottom: 6px !important;
    }
    /* NOUVEAU (22/08/2026) : titres et libellés de la sidebar ("+
       Nouveau", "Filtrer par", "Université", "UFR / Institut",
       "Département", "Spécialité"...) en gras noir -- plus lisibles,
       plus nets. */
    .sidebar h4,
    .sidebar .form-label,
    .sidebar label {
        font-weight: 700 !important;
        color: #1a1a1a !important;
    }
    .sidebar h4 {
        font-size: 1rem !important;
        text-align: center;
        margin-top: 4px !important;
        margin-bottom: 4px !important;
    }
    .sidebar hr {
        margin: 8px 0 !important;
    }
    .sidebar .btn {
        margin-bottom: 0 !important;
    }
    /* ---- Crayon (modifier) + copier sous les questions de l'utilisateur ---- */
    .mbf-msg-toolbar {
        display: flex;
        gap: 2px;
        margin: 0 0 8px 0;
        justify-content: flex-end;
    }
    .mbf-icon-btn {
        background: none;
        border: none;
        cursor: pointer;
        color: #6e6e80;
        opacity: 0.65;
        padding: 3px;
        border-radius: 4px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        line-height: 0;
    }
    .mbf-icon-btn svg {
        width: 14px;
        height: 14px;
        display: block;
    }
    .mbf-icon-btn:hover {
        opacity: 1;
        color: #2b2b33;
        background: #f0f0f0;
    }
    /* ---- Questions suggérées (bulles cliquables) sous une réponse du chat ---- */
    .mbf-suggestions-bar {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 4px 0 16px 0;
    }
    .mbf-suggestion-chip {
        background: #f5f5f7;
        border: 1px solid #ddd;
        border-radius: 16px;
        padding: 5px 12px;
        font-size: 0.82em;
        color: #333;
        cursor: pointer;
        line-height: 1.3;
    }
    .mbf-suggestion-chip:hover {
        background: #eaeaea;
        border-color: #bbb;
    }
    .mbf-suggestion-chip-custom {
        border-style: dashed;
        color: #666;
        display: inline-flex;
        align-items: center;
        gap: 4px;
    }
    .mbf-suggestion-chip-custom svg {
        width: 12px;
        height: 12px;
    }
    .mbf-edit-panel {
        margin: 4px 0 12px 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .mbf-edit-textarea {
        min-height: 70px;
        font-size: 0.9em;
    }
    .mbf-edit-btn-row {
        display: flex;
        gap: 6px;
        justify-content: flex-end;
    }
""")

# =========================================================================
# INDICATEUR DE CONNEXION EN HAUT À DROITE (nouveau, 20/08/2026)
#
# Fonction top-level @render.ui, volontairement PAS associée à un
# ui.output_ui() explicite -- elle s'auto-place via le mécanisme
# implicite d'Express (comme zone_principale/sidebar_contenu plus bas),
# et est ensuite épinglée visuellement en haut à droite par CSS
# (#mbf_topright, voir juste au-dessus) peu importe sa position réelle
# dans le HTML. Ne montre JAMAIS le nom d'utilisateur -- juste un état
# "connecté" ou un bouton "Connexion".
# =========================================================================
@render.ui
def zone_connexion_topright():
    if current_user() is None:
        return ui.div(
            ui.input_action_button("btn_ouvrir_connexion", "Connexion", class_="btn-outline-light"),
            id="mbf_topright",
        )
    return ui.div(
        ui.input_action_button(
            "btn_topright_connecte", "🟢 Connecté",
            class_="btn-outline-light", title="Cliquer pour se déconnecter",
        ),
        id="mbf_topright",
    )


# =========================================================================
# RENDU LATEX (KaTeX, réintégré 21/08/2026 -- v2, migration OpenAI->Claude)
# shinychat n'a AUCUN support LaTeX intégré (pas de KaTeX/MathJax/
# remark-math dans son JS). Une première tentative avait été abandonnée
# avec GPT-4.1-mini, qui ne respectait pas fiablement les délimiteurs
# $ ... $ (recopiait le parenthésage brut du PDF source à la place).
# Réintégré maintenant que chat_client/resume_client tournent sur Claude,
# qui respecte beaucoup plus fiablement une consigne de syntaxe stricte
# comme celle-ci (voir prompt.txt, section FORMULES MATHÉMATIQUES).
# On charge KaTeX + son extension "auto-render" via CDN, déclenchée sur
# chaque nouveau message dans mbfSetup() ci-dessous.
# =========================================================================
ui.tags.link(
    rel="stylesheet",
    href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.css",
)
ui.tags.script(src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.js")
ui.tags.script(src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/contrib/auto-render.min.js")


# =========================================================================
# CRAYON (modifier) + COPIER sous chaque question de l'utilisateur.
#
# Le composant ui.Chat ne permet pas nativement d'ajouter des boutons sous
# chaque bulle de message : on le fait donc côté client, en JS, en
# surveillant l'apparition des messages <shiny-chat-message data-role="user">
# dans le DOM (MutationObserver, car les messages arrivent en streaming/
# après coup, pas au chargement de la page).
#
# - Copier : copie le texte brut de la question dans le presse-papier.
# - Crayon : remplace l'affichage par une zone de texte modifiable, avec un
#   bouton "Enregistrer et renvoyer". La question éditée n'écrase PAS
#   l'ancienne bulle (on ne retouche pas l'historique interne du composant
#   Chat, trop fragile) -- elle est renvoyée comme un NOUVEAU message tout
#   en bas de la conversation, exactement comme si l'étudiant l'avait
#   retapée et validée.
#
# NOTE DE COMPATIBILITE : le sélecteur ci-dessous cible les versions
# récentes de shinychat, qui utilisent <shiny-chat-message data-role="user">.
# Si les icônes n'apparaissent pas, inspecte une bulle de question dans les
# outils dev du navigateur : si tu vois plutôt <shiny-user-message>, change
# le sélecteur JS en conséquence.
# =========================================================================
ui.tags.script(r"""
(function() {
    function mbfSetup() {
        const processed = new WeakSet();

        // Icônes SVG monochromes (héritent de "color" en CSS -- gris discret,
        // pas d'emoji colorés) façon icônes fines type "feather".
        const ICON_PENCIL =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"></path>' +
            '<path d="m15 5 4 4"></path></svg>';
        const ICON_COPY =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round">' +
            '<rect x="9" y="9" width="13" height="13" rx="2"></rect>' +
            '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
        const ICON_CHECK =
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

        function makeToolbar(msgEl) {
            const bar = document.createElement("div");
            bar.className = "mbf-msg-toolbar";

            const btnEdit = document.createElement("button");
            btnEdit.type = "button";
            btnEdit.className = "mbf-icon-btn";
            btnEdit.title = "Modifier la question";
            btnEdit.innerHTML = ICON_PENCIL;
            btnEdit.onclick = () => openEditor(msgEl, bar);

            const btnCopy = document.createElement("button");
            btnCopy.type = "button";
            btnCopy.className = "mbf-icon-btn";
            btnCopy.title = "Copier la question";
            btnCopy.innerHTML = ICON_COPY;
            btnCopy.onclick = () => {
                navigator.clipboard.writeText(msgEl.innerText.trim()).then(() => {
                    btnCopy.innerHTML = ICON_CHECK; // feedback bref
                    setTimeout(() => { btnCopy.innerHTML = ICON_COPY; }, 1200);
                });
            };

            bar.appendChild(btnEdit);
            bar.appendChild(btnCopy);
            return bar;
        }

        function openEditor(msgEl, bar) {
            // évite d'ouvrir deux panneaux d'édition sur la même question
            const existing = bar.nextElementSibling;
            if (existing && existing.classList.contains("mbf-edit-panel")) return;

            const panel = document.createElement("div");
            panel.className = "mbf-edit-panel";

            const textarea = document.createElement("textarea");
            textarea.className = "form-control mbf-edit-textarea";
            textarea.value = msgEl.innerText.trim();

            const btnRow = document.createElement("div");
            btnRow.className = "mbf-edit-btn-row";

            const btnSave = document.createElement("button");
            btnSave.type = "button";
            btnSave.className = "btn btn-primary btn-sm";
            btnSave.textContent = "Enregistrer et renvoyer";
            btnSave.onclick = () => {
                const texte = textarea.value.trim();
                if (!texte) return;
                Shiny.setInputValue(
                    "mbf_edited_question",
                    { text: texte, ts: Date.now() },
                    { priority: "event" }
                );
                panel.remove();
            };

            const btnCancel = document.createElement("button");
            btnCancel.type = "button";
            btnCancel.className = "btn btn-outline-secondary btn-sm";
            btnCancel.textContent = "Annuler";
            btnCancel.onclick = () => panel.remove();

            btnRow.appendChild(btnSave);
            btnRow.appendChild(btnCancel);
            panel.appendChild(textarea);
            panel.appendChild(btnRow);

            bar.insertAdjacentElement("afterend", panel);
            textarea.focus();
        }

        function processMessages() {
            document.querySelectorAll('shiny-chat-message[data-role="user"]').forEach((msgEl) => {
                if (processed.has(msgEl)) return;
                processed.add(msgEl);
                msgEl.insertAdjacentElement("afterend", makeToolbar(msgEl));
                masquerHeroAccueil(); // premier message envoyé -> écran d'accueil disparaît
            });
        }

        // Écran d'accueil (logo + titre + texte) affiché tant qu'aucun
        // message n'a été envoyé (voir #mbf_hero_accueil, statique dans
        // le HTML). Masqué en fondu dès le premier message utilisateur,
        // réaffiché via le message custom "mbf_hero" (utilisé par
        // "Nouvelle conversation", voir handler plus bas).
        function masquerHeroAccueil() {
            const hero = document.getElementById("mbf_hero_accueil");
            if (hero) hero.classList.add("mbf-hero-hidden");
        }
        function afficherHeroAccueil() {
            const hero = document.getElementById("mbf_hero_accueil");
            if (hero) hero.classList.remove("mbf-hero-hidden");
        }

        // -----------------------------------------------------------------
        // ÉCRAN DE CONNEXION -- effet "machine à écrire" sur le texte de
        // présentation (#mbf_login_texte, voir zone_principale()). Le
        // texte complet est dans l'attribut data-text (jamais affiché
        // d'un coup) ; on le tape caractère par caractère. Ne se lance
        // qu'UNE FOIS par apparition de l'écran de connexion (flag
        // data-mbf-typed), pour ne pas relancer l'animation à chaque
        // petite mise à jour réactive de zone_principale().
        function lancerMachineAEcrire() {
            const el = document.getElementById("mbf_login_texte");
            if (!el || el.dataset.mbfTyped === "true") return;
            el.dataset.mbfTyped = "true";

            const texteComplet = el.getAttribute("data-text") || "";
            el.textContent = "";
            el.classList.add("mbf-typing");

            let i = 0;
            const vitesse = 18; // ms par caractère
            function taperSuite() {
                if (i >= texteComplet.length) {
                    el.classList.remove("mbf-typing");
                    return;
                }
                el.textContent += texteComplet.charAt(i);
                i++;
                setTimeout(taperSuite, vitesse);
            }
            taperSuite();
        }

        // -----------------------------------------------------------------
        // QUESTIONS SUGGÉRÉES (bulles cliquables) sous une réponse du chat.
        //
        // Le PROMPT (voir prompt.txt) demande au modèle de terminer sa
        // réponse par une ligne technique, du type :
        //     MBF_SUGGESTIONS::["Question 1","Question 2","Question 3"]
        // uniquement quand la réponse concerne UN enseignant précis.
        //
        // On la détecte via innerText (texte visible, donc fiable même si
        // le rendu markdown -> HTML nettoie les commentaires HTML), on la
        // MASQUE (l'utilisateur ne doit jamais voir cette ligne technique),
        // et on affiche à la place de vraies bulles cliquables. Un clic
        // envoie la question comme si elle avait été tapée (même mécanisme
        // que "modifier la question", voir Shiny.setInputValue plus haut).
        //
        // On ne marque un message "traité" QUE si on a trouvé la ligne (ou
        // un JSON invalide) -- tant que rien n'est trouvé, on retente au
        // prochain mutation event, ce qui gère naturellement le cas où le
        // message est encore en train d'arriver en streaming.
        // -----------------------------------------------------------------
        // Ouvre un petit champ de texte libre sous la barre de suggestions,
        // pour que l'étudiant puisse taper sa PROPRE question de suivi sans
        // redescendre jusqu'à la zone de saisie principale du chat (réutilise
        // le même style que le panneau d'édition du crayon plus haut).
        function ouvrirQuestionPerso(bar) {
            const existant = bar.nextElementSibling;
            if (existant && existant.classList.contains("mbf-edit-panel")) return;

            const panel = document.createElement("div");
            panel.className = "mbf-edit-panel";

            const textarea = document.createElement("textarea");
            textarea.className = "form-control mbf-edit-textarea";
            textarea.placeholder = "Écris ta question ici...";

            const btnRow = document.createElement("div");
            btnRow.className = "mbf-edit-btn-row";

            const btnEnvoyer = document.createElement("button");
            btnEnvoyer.type = "button";
            btnEnvoyer.className = "btn btn-primary btn-sm";
            btnEnvoyer.textContent = "Envoyer";
            btnEnvoyer.onclick = () => {
                const texte = textarea.value.trim();
                if (!texte) return;
                Shiny.setInputValue(
                    "mbf_edited_question",
                    { text: texte, ts: Date.now() },
                    { priority: "event" }
                );
                panel.remove();
                bar.remove();
            };

            const btnAnnuler = document.createElement("button");
            btnAnnuler.type = "button";
            btnAnnuler.className = "btn btn-outline-secondary btn-sm";
            btnAnnuler.textContent = "Annuler";
            btnAnnuler.onclick = () => panel.remove();

            btnRow.appendChild(btnEnvoyer);
            btnRow.appendChild(btnAnnuler);
            panel.appendChild(textarea);
            panel.appendChild(btnRow);

            bar.insertAdjacentElement("afterend", panel);
            textarea.focus();
        }

        function processSuggestions() {
            document.querySelectorAll('shiny-chat-message[data-role="assistant"]').forEach((msgEl) => {
                if (msgEl.dataset.mbfProcessed === "true") return;

                // BUG CORRIGÉ (22/08/2026) : le hero (logo + texte
                // d'accueil) ne se masquait que sur un message UTILISATEUR
                // (voir processMessages() plus haut) -- or l'envoi d'un
                // PDF/photo ajoute directement un message ASSISTANT (le
                // résumé), sans qu'aucun message utilisateur n'ait été
                // tapé. Le hero restait donc affiché, superposé au résumé.
                // On le masque ici aussi, dès qu'un message assistant
                // apparaît (peu importe lequel).
                masquerHeroAccueil();

                const texte = msgEl.innerText || "";

                // Résumé de document envoyé (PDF/photo) : coloration
                // distincte du message + icône dédiée (voir CSS), pour
                // qu'on ne le confonde jamais avec une réponse normale
                // du chatbot. Traité indépendamment de MBF_SUGGESTIONS
                // ci-dessous, mais les deux arrivent toujours ensemble
                // pour un vrai résumé d'article (voir resume_client).
                if (texte.includes("MBF_DOC_SUMMARY::true") && !msgEl.dataset.mbfDocStyled) {
                    msgEl.classList.add("mbf-doc-summary-message");
                    const walkerDoc = document.createTreeWalker(msgEl, NodeFilter.SHOW_TEXT);
                    let nodeDoc;
                    while ((nodeDoc = walkerDoc.nextNode())) {
                        if (nodeDoc.nodeValue.includes("MBF_DOC_SUMMARY::true")) {
                            const cibleDoc = nodeDoc.parentElement;
                            if (cibleDoc) cibleDoc.style.display = "none";
                            break;
                        }
                    }
                    msgEl.dataset.mbfDocStyled = "true";
                }

                const m = texte.match(/MBF_SUGGESTIONS::(\[[\s\S]*?\])/);
                if (!m) return; // pas encore arrivé -- on réessaiera plus tard

                let suggestions = [];
                try {
                    suggestions = JSON.parse(m[1]);
                } catch (e) {
                    msgEl.dataset.mbfProcessed = "true"; // JSON invalide, on abandonne pour ce message
                    return;
                }

                // Masque la ligne technique dans l'affichage : on cherche le
                // nœud de texte qui la contient et on cache son élément parent.
                const walker = document.createTreeWalker(msgEl, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    if (node.nodeValue.includes("MBF_SUGGESTIONS::")) {
                        const cible = node.parentElement;
                        if (cible) cible.style.display = "none";
                        break;
                    }
                }

                if (Array.isArray(suggestions)) {
                    const bar = document.createElement("div");
                    bar.className = "mbf-suggestions-bar";
                    suggestions.forEach((texteQuestion) => {
                        if (!texteQuestion) return;
                        const chip = document.createElement("button");
                        chip.type = "button";
                        chip.className = "mbf-suggestion-chip";
                        chip.textContent = texteQuestion;
                        chip.onclick = () => {
                            Shiny.setInputValue(
                                "mbf_edited_question",
                                { text: texteQuestion, ts: Date.now() },
                                { priority: "event" }
                            );
                            bar.remove();
                        };
                        bar.appendChild(chip);
                    });

                    // 4e bulle FIXE, toujours ajoutée en plus des 2-3 proposées
                    // par le modèle -- permet de poser une question libre
                    // directement ici (façon "+ Autre question").
                    const chipPerso = document.createElement("button");
                    chipPerso.type = "button";
                    chipPerso.className = "mbf-suggestion-chip mbf-suggestion-chip-custom";
                    chipPerso.innerHTML = ICON_PENCIL + "<span>Poser une autre question</span>";
                    chipPerso.onclick = () => ouvrirQuestionPerso(bar);
                    bar.appendChild(chipPerso);

                    msgEl.insertAdjacentElement("afterend", bar);
                }

                msgEl.dataset.mbfProcessed = "true";
            });
        }

        // BUG CORRIGÉ (22/08/2026) : "Cette page ne répond pas" -- pendant
        // le streaming d'une réponse riche en formules, le DOM change très
        // fréquemment (plusieurs fois par seconde), et chaque changement
        // relançait processMessages()/processSuggestions()/renderLatex()/
        // lancerMachineAEcrire() en entier -- renderLatex() en particulier
        // (KaTeX) redevient coûteux si on le redéclenche des dizaines de
        // fois par seconde sur un message qui grossit avec beaucoup de
        // formules. On LIMITE (throttle) ces traitements à au plus un
        // passage toutes les ~120ms : imperceptible pour l'utilisateur,
        // mais ça évite de saturer le thread principal du navigateur au
        // point de le rendre inerte.
        function mbfExecuterTraitements() {
            processMessages();
            processSuggestions();
            renderLatex();
            lancerMachineAEcrire();
        }

        let mbfThrottleTimer = null;
        let mbfThrottleEnAttente = false;
        function mbfTraitementsThrottled() {
            if (mbfThrottleTimer) {
                mbfThrottleEnAttente = true;
                return;
            }
            mbfExecuterTraitements();
            mbfThrottleTimer = setTimeout(() => {
                mbfThrottleTimer = null;
                if (mbfThrottleEnAttente) {
                    mbfThrottleEnAttente = false;
                    mbfTraitementsThrottled();
                }
            }, 120);
        }

        mbfExecuterTraitements();
        new MutationObserver(() => {
            mbfTraitementsThrottled();
        }).observe(document.body, { childList: true, subtree: true });

        // Rendu LaTeX (KaTeX) -- voir le commentaire au-dessus du
        // chargement des <script>/<link> CDN plus haut dans le fichier.
        // Idempotent : KaTeX marque le texte déjà composé, donc rappeler
        // cette fonction sur tout document.body à chaque mutation
        // (streaming compris) ne re-traite jamais deux fois la même
        // formule.
        function renderLatex() {
            if (!window.renderMathInElement) return; // CDN pas encore chargé
            // NOUVEAU (22/08/2026) : on ne rescane que ce qui n'a pas
            // encore été marqué "terminé", au lieu de tout document.body
            // à chaque mutation -- rescanner toute la page en boucle (le
            // MutationObserver se déclenche très souvent) devenait très
            // lent avec une longue conversation ou une réponse
            // d'historique volumineuse rejouée d'un coup (voir "Lancer"
            // dans l'historique), donnant l'impression que l'appli se
            // figeait / ne répondait plus.
            //
            // Le TOUT DERNIER message est TOUJOURS retraité (jamais
            // marqué "terminé") : c'est lui qui peut encore être en
            // train d'arriver en streaming, donc son texte -- et les
            // formules qu'il contient -- continue de changer. Coût
            // négligeable puisqu'un seul message, pas toute la page.
            const tousMessages = document.querySelectorAll("shiny-chat-message");
            tousMessages.forEach((msgEl, index) => {
                const estDernier = index === tousMessages.length - 1;
                if (msgEl.dataset.katexDone === "true" && !estDernier) return;

                // BUG CORRIGÉ (05/09/2026) : la version précédente
                // réassignait tout le innerHTML du message ("msgEl.innerHTML
                // = ...") pour corriger le underscore manquant / reconvertir
                // %%US%% -- sur le DERNIER message (encore en streaming),
                // ça entre en conflit avec React (le moteur interne de
                // shinychat), qui gère ce même message en parallèle : le
                // remplacement brutal du innerHTML lui fait perdre le fil
                // de ce qu'il a déjà rendu, et il duplique du contenu au
                // prochain morceau reçu (paragraphes entiers répétés).
                // Corrigé en ne touchant plus JAMAIS innerHTML -- seulement
                // le texte des nœuds individuels (node.nodeValue), une
                // opération beaucoup plus légère que React tolère bien
                // (c'est d'ailleurs ce que KaTeX lui-même fait déjà sans
                // souci pour composer les formules).
                mbfCorrigerLatexManquant(msgEl);

                window.renderMathInElement(msgEl, {
                    delimiters: [
                        { left: "$$", right: "$$", display: true },
                        { left: "\\[", right: "\\]", display: true },
                        { left: "\\(", right: "\\)", display: false },
                        { left: "$", right: "$", display: false }
                    ],
                    throwOnError: false,
                });
                if (!estDernier) msgEl.dataset.katexDone = "true";
            });
        }


        // \hat \bar \tilde \vec \dot ne prennent qu'UN SEUL argument --
        // une accolade supplémentaire juste après ne peut être qu'un
        // indice oublié. \sum \int \lim \prod ne prennent AUCUNE accolade
        // directement -- pareil, une accolade juste après est forcément
        // un indice oublié. \frac (2 arguments légitimes) n'est JAMAIS
        // touché par ces deux corrections. On reconvertit aussi %%US%%
        // (underscore protégé côté Python, voir _proteger_underscores_maths)
        // en underscore réel, juste avant KaTeX.
        //
        // IMPORTANT : opère UNIQUEMENT sur node.nodeValue de chaque nœud
        // texte (jamais innerHTML) -- voir le commentaire dans renderLatex()
        // ci-dessus pour pourquoi la version innerHTML causait des
        // duplications de contenu pendant le streaming.
        function mbfCorrigerLatexManquant(rootEl) {
            const walker = document.createTreeWalker(rootEl, NodeFilter.SHOW_TEXT);
            let node;
            while ((node = walker.nextNode())) {
                const original = node.nodeValue;
                let corrige = original.replace(/\\(hat|bar|tilde|vec|dot)(\{[^{}]*\})\{/g, "\\$1$2_{");
                corrige = corrige.replace(/\\(sum|int|prod|lim)\{/g, "\\$1_{");
                corrige = corrige.replace(/%%US%%/g, "_");
                if (corrige !== original) {
                    node.nodeValue = corrige;
                }
            }
        }


        // Overlay de chargement (spinner centré) -- basculé par message
        // custom envoyé depuis le serveur (_busy_afficher()/_busy_masquer()
        // dans Mentors.py), PAS par re-rendu réactif classique : livraison
        // immédiate garantie, indépendante du cycle de flush de Shiny (voir
        // le commentaire détaillé au-dessus de _busy_afficher() côté Python).
        if (window.Shiny && Shiny.addCustomMessageHandler) {
            Shiny.addCustomMessageHandler("mbf_busy", function(msg) {
                const overlay = document.getElementById("mbf_overlay_busy");
                if (!overlay) return;
                if (msg.show) {
                    const texteEl = overlay.querySelector(".mbf-spinner-texte");
                    if (texteEl) texteEl.textContent = msg.texte || "Chargement en cours...";
                    overlay.style.display = "flex";
                } else {
                    overlay.style.display = "none";
                }
            });

            // Réaffiche l'écran d'accueil (logo + texte) -- utilisé par
            // "Nouvelle conversation" côté serveur (chat.clear_messages()
            // vide le chat, mais ne fait pas réapparaître le hero tout
            // seul puisqu'il n'est plus lié au premier message du chat).
            Shiny.addCustomMessageHandler("mbf_hero", function(msg) {
                if (msg.show) {
                    afficherHeroAccueil();
                } else {
                    masquerHeroAccueil();
                }
            });

            // NOUVEAU (08/09/2026) : retire ?code=...&state=... de l'URL
            // après une connexion Google réussie (voir
            // _traiter_retour_google() côté Python) -- sans ça, un
            // rafraîchissement de la page retraiterait le même code
            // (déjà utilisé, Google le refuserait). history.replaceState
            // ne recharge PAS la page, juste l'URL visible change.
            Shiny.addCustomMessageHandler("mbf_nettoyer_url", function(msg) {
                const urlPropre = window.location.origin + window.location.pathname;
                window.history.replaceState({}, document.title, urlPropre);
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", mbfSetup);
    } else {
        mbfSetup();
    }
})();
""")


# =========================================================================
# AUTHENTIFICATION : état de session (partagé par tout le module)
# =========================================================================
# current_user : None tant que personne n'est connecté, sinon le nom
# d'utilisateur (str). C'est cette valeur qui décide si on affiche le
# formulaire de connexion ou l'application complète.
current_user = reactive.Value(None)
auth_message = reactive.Value("")
# Incrémenté à chaque ajout/suppression d'historique, pour forcer
# sidebar_contenu() à se ré-exécuter (get_history() lit directement la
# base SQLite, donc Shiny n'a pas d'autre moyen de savoir qu'il faut
# rafraîchir l'affichage).
historique_version = reactive.Value(0)

# NOUVEAU : le "state" est toujours envoyé à Google (bonne pratique,
# et utile si on veut un jour l'exploiter), mais PAS vérifié au retour --
# voir le commentaire détaillé juste avant _traiter_retour_google() pour
# pourquoi cette vérification ne peut structurellement pas fonctionner
# avec le modèle de session de Shiny (bug identifié le 08/09/2026).
google_oauth_state = reactive.Value(None)
# Empêche de retraiter le même ?code=... plusieurs fois (le reactive
# effect ci-dessous peut se redéclencher pour d'autres raisons tant que
# l'URL contient encore le code, avant qu'on ait pu la nettoyer).
google_oauth_traite = reactive.Value(False)


# =========================================================================
# BUG IDENTIFIÉ ET CORRIGÉ (08/09/2026) : le bouton "Continuer avec
# Google" est un vrai lien <a href="https://accounts.google.com/...">
# (nécessaire pour forcer une vraie navigation de page, pas une requête
# AJAX) -- cliquer dessus fait donc QUITTER complètement l'app Shiny, ce
# qui TUE la session en cours (et tout ce qui y était stocké en mémoire,
# dont google_oauth_state). Quand Google redirige ensuite vers
# http://127.0.0.1:8001/?code=...&state=..., Shiny démarre une TOUTE
# NOUVELLE session, qui ne se souvient plus du state généré juste avant
# dans l'ancienne. Une vérification stricte state_recu == state_stocké
# échoue donc SYSTÉMATIQUEMENT, silencieusement -- c'est ce qui bloquait
# la connexion. Corrigé en ne vérifiant plus ce state au retour (la
# protection reste correcte : Google ne redirige QUE vers l'URI
# enregistrée dans la Google Cloud Console, et le code d'autorisation
# est à usage unique et de très courte durée de vie).
# =========================================================================
@reactive.effect
async def _traiter_retour_google():
    """Se déclenche dès que l'URL contient ?code=...&state=... (retour
    de Google après connexion) -- échange le code, récupère l'identité,
    connecte l'utilisateur. session.clientdata.url_search() est l'API
    PUBLIQUE documentée de Shiny pour lire la chaîne de requête de l'URL
    (voir commentaire détaillé plus haut sur le choix d'architecture)."""
    if not _google_active() or google_oauth_traite():
        return

    recherche = session.clientdata.url_search()
    if not recherche or "code=" not in recherche:
        return

    params = parse_qs(recherche.lstrip("?"))
    code = (params.get("code") or [None])[0]
    if not code:
        return

    google_oauth_traite.set(True)  # une seule tentative, même si ça échoue

    try:
        jetons = _echanger_code_google(code)
        infos = _infos_utilisateur_google(jetons["access_token"])
    except Exception as err:
        auth_message.set(f"Erreur lors de la connexion Google : {err}")
        return

    email = (infos.get("email") or "").strip().lower()
    if not email:
        auth_message.set("Google n'a pas renvoyé d'adresse email exploitable.")
        return

    nom_complet = infos.get("name") or ""
    username = auth.get_or_create_google_user(email, nom_complet)
    current_user.set(username)
    auth_message.set("")

    # Nettoie l'URL (retire ?code=...&state=...) pour ne pas retraiter
    # le même code si l'utilisateur rafraîchit la page ensuite.
    await session.send_custom_message("mbf_nettoyer_url", {})

# Résultats de la liste consultable (filtres, sans IA) -- affichés dans
# la zone principale, au-dessus du chat, quand non vide.
liste_resultats = reactive.Value([])
# True dès que "Voir la liste" a été cliqué au moins une fois, pour
# distinguer "pas encore cliqué" de "cliqué, mais 0 résultat" (sinon les
# deux cas semblent identiques à l'écran : rien ne s'affiche).
liste_active = reactive.Value(False)
# NOUVEAU (20/08/2026) : résumé du dernier document envoyé (PDF/photo),
# affiché sous le bouton d'envoi. Vide tant qu'aucun document n'a été
# envoyé dans cette session.
resume_upload = reactive.Value("")
# NOUVEAU (21/08/2026) : contenu exploitable du DERNIER document envoyé
# reconnu comme article scientifique -- {"nom_fichier", "type", "contenu"}
# ou None. Sert à répondre aux questions de suivi posées dans le chat
# (ex: en cliquant une question suggérée par resume_client après l'upload)
# -- SANS ça, chat_client (qui gère ces questions) n'a JAMAIS vu le texte
# du document, seulement les fiches mentors du RAG : voir _repondre()
# plus bas, et la section correspondante ajoutée dans prompt.txt.
contexte_document = reactive.Value(None)


# =========================================================================
# OVERLAY DE CHARGEMENT (nouveau, 21/08/2026 -- v2, message custom)
#
# BUG CORRIGÉ : la première version utilisait une reactive.Value +
# @render.ui (comme zone_upload_document). Ça ne s'affichait JAMAIS à
# l'écran, même brièvement : le cycle de "flush" réactif de Shiny pour
# un output @render.ui ne livre pas forcément chaque .set() intermédiaire
# au navigateur en temps réel pendant qu'un effet asynchrone tourne --
# souvent, seul l'état FINAL (donc déjà remis à None par le bloc finally)
# est envoyé. C'est exactement pour cette raison que shinychat lui-même
# n'utilise PAS ce mécanisme pour le streaming des réponses (sinon le
# texte n'apparaîtrait pas token par token) : il envoie directement des
# messages custom au client. On fait pareil ici, via
# session.send_custom_message() + un handler JS (voir mbfSetup plus
# haut) qui bascule un simple <div> déjà présent dans la page -- livraison
# immédiate, indépendante du cycle de rendu réactif.
# =========================================================================
async def _busy_afficher(texte: str):
    await session.send_custom_message("mbf_busy", {"show": True, "texte": texte})


async def _busy_masquer():
    await session.send_custom_message("mbf_busy", {"show": False})



# =========================================================================
# CONNEXION / INSCRIPTION -- architecture en 2 ÉTAPES (20/08/2026,
# refonte complète). PLUS AUCUN render.ui/output_ui imbriqué (c'est ce
# qui causait le bug "Duplicate output ID" du système précédent -- les
# fonctions @render.ui définies au niveau module s'auto-placent
# implicitement via Express, ce qui entrait en collision avec les
# output_ui() explicites). Ici, chaque modale est construite une seule
# fois par du code Python ordinaire, avec un contenu figé au moment de
# l'ouverture -- pas de champs qui apparaissent après coup.
#
#   Étape 1 : "Se connecter" (compte existant) à gauche, "Vous êtes"
#             (nouveau compte) à droite -- face à face.
#   Étape 2 : nom d'utilisateur* + mot de passe* + confirmation*, puis
#             les champs de la catégorie choisie (tous affichés
#             ensemble -- ex: pour "Élève", Niveau ET Série sont montrés
#             en même temps plutôt que Série qui n'apparaîtrait qu'après
#             coup, pour rester simple et fiable).
# =========================================================================

def _label_obligatoire(texte):
    """Astérisque rouge pour les champs réellement obligatoires (nom
    d'utilisateur, mot de passe, confirmation) -- tout le reste du
    profil est facultatif."""
    return ui.tags.span(texte, " ", ui.tags.span("*", style="color:#b00020; font-weight:700;"))



def _construire_modal_etape1():
    """Écran de connexion minimaliste, façon Claude/ChatGPT : nom
    d'utilisateur + mot de passe uniquement. Google n'apparaît plus ici
    (déjà visible sur l'écran d'accueil juste en dessous -- inutile de
    le dupliquer dans cette modale, 08/09/2026)."""
    return ui.modal(
        ui.div(
            ui.div(
                ui.h5("Se connecter", class_="mbf-auth-titre"),
                ui.input_text("login_username", _label_obligatoire("Nom d'utilisateur")),
                ui.input_password("login_password", _label_obligatoire("Mot de passe")),
                ui.input_action_button("btn_login", "Se connecter", class_="btn-primary w-100"),
            ),
            ui.div(
                ui.h5("Créer un compte", class_="mbf-auth-titre"),
                ui.input_text("signup_username", _label_obligatoire("Nom d'utilisateur")),
                ui.input_password("signup_password", _label_obligatoire("Mot de passe")),
                ui.input_password("signup_password_confirm", _label_obligatoire("Confirmer le mot de passe")),
                ui.input_action_button("btn_creer_compte", "Créer un compte", class_="btn-primary w-100"),
            ),
            class_="mbf-auth-colonnes",
        ),
        ui.br(),
        ui.p(auth_message(), style="color:#b00020; font-weight:600;") if auth_message() else None,
        title="Connexion à MentorBF",
        easy_close=True,
        size="l",
        footer=ui.modal_button("Fermer"),
    )


@reactive.effect
@reactive.event(input.btn_ouvrir_connexion)
def _():
    auth_message.set("")
    ui.modal_show(_construire_modal_etape1())


@reactive.effect
@reactive.event(input.btn_ouvrir_connexion_centre)
def _():
    """Bouton de convenance dans la zone principale (en plus de
    l'indicateur en haut à droite) -- ouvre exactement la même modale."""
    auth_message.set("")
    ui.modal_show(_construire_modal_etape1())


@reactive.effect
@reactive.event(input.btn_login)
def _():
    ok, msg = auth.verify_user(input.login_username(), input.login_password())
    auth_message.set(msg)
    if ok:
        current_user.set(input.login_username().strip())
        ui.modal_remove()
    else:
        ui.modal_show(_construire_modal_etape1())


@reactive.effect
@reactive.event(input.btn_creer_compte)
def _():
    # NOUVEAU (08/09/2026) : inscription minimaliste -- juste nom
    # d'utilisateur + mot de passe. Le reste du profil (catégorie, pays,
    # nationalité, niveau, université...) se remplit ENSUITE, uniquement
    # via "Modifier mon profil" une fois connecté -- jamais imposé à
    # l'inscription.
    if input.signup_password() != input.signup_password_confirm():
        auth_message.set("Les deux mots de passe ne correspondent pas.")
        ui.modal_show(_construire_modal_etape1())
        return

    ok, msg = auth.create_user(input.signup_username(), input.signup_password())
    auth_message.set(msg)
    if not ok:
        ui.modal_show(_construire_modal_etape1())
        return

    current_user.set(input.signup_username().strip())
    ui.modal_remove()

@reactive.effect
@reactive.event(input.btn_topright_connecte)
async def _():
    """Clic sur l'indicateur 'Connecté' lui-même -- déconnexion directe
    (remplace l'ancien bouton 'Se déconnecter' de la sidebar)."""
    current_user.set(None)
    auth_message.set("")
    await chat.clear_messages()


# =========================================================================

# SIDEBAR : filtres + historique (uniquement visible une fois connecté)
# =========================================================================
with ui.sidebar(width=300):

    @render.ui
    def sidebar_contenu():
        if current_user() is None:
            return ui.p("Connectez-vous pour accéder aux filtres et à votre historique.")

        _ = historique_version()  # dépendance réactive : force le rafraîchissement
        historique = auth.get_history(current_user(), limit=30)

        # NOUVEAU (22/08/2026 -- v2) : select compact, comme Université/
        # UFR, plutôt qu'une liste de cartes dépliables -- qui ne passe
        # pas à l'échelle si l'utilisateur a beaucoup de recherches
        # enregistrées (defilement sans fin dans la sidebar). On choisit
        # une entrée dans la liste déroulante, puis deux petites icônes
        # "▶ Lancer" / "🗑 Supprimer" agissent sur l'entrée sélectionnée.
        if historique:
            choix_historique = {
                str(h["id"]): (
                    f"{h['query_text'][:38]}{'…' if len(h['query_text']) > 38 else ''} "
                    f"— {h['created_at'][:16].replace('T', ' ')}"
                )
                for h in historique
            }
            bloc_historique = [
                ui.hr(),
                ui.h4("Mon historique", style="margin-bottom: 4px;"),
                ui.input_select("historique_select", None, choices=choix_historique),
                ui.div(
                    ui.input_action_button(
                        "btn_historique_lancer", "▶ Lancer",
                        class_="mbf-hist-btn mbf-hist-btn-lancer",
                    ),
                    ui.input_action_button(
                        "btn_historique_supprimer", "🗑 Supprimer",
                        class_="mbf-hist-btn mbf-hist-btn-supprimer",
                    ),
                    class_="mbf-hist-actions",
                ),
            ]
        else:
            bloc_historique = [
                ui.hr(),
                ui.h4("Mon historique", style="margin-bottom: 4px;"),
                ui.p(
                    "Aucune recherche enregistrée pour l'instant.",
                    style="font-size: 0.8rem; color: #999;",
                ),
            ]

        # Choix d'UFR dépendant de l'université sélectionnée. On lit
        # input.universite() ici (au lieu d'un output_ui séparé, absent en
        # mode shiny.express) : cela rend sidebar_contenu() dépendante de
        # ce choix, donc elle se ré-exécute automatiquement quand l'utilisateur
        # change d'université.
        try:
            univ_selectionnee = input.universite()
        except Exception:
            univ_selectionnee = "Toutes les universités"
        if univ_selectionnee == "Toutes les universités":
            ufrs = ["Toutes les UFR"]
        else:
            ufrs = ["Toutes les UFR"] + UFR_PAR_UNIVERSITE.get(univ_selectionnee, [])

        # Département dépendant à son tour de l'UFR sélectionné -- même
        # principe de cascade que Université -> UFR ci-dessus. On lit
        # input.ufr() pour que sidebar_contenu() se ré-exécute aussi quand
        # l'UFR change (pas seulement l'université).
        try:
            ufr_selectionnee = input.ufr()
        except Exception:
            ufr_selectionnee = "Toutes les UFR"

        options_departement = departements_disponibles(univ_selectionnee, ufr_selectionnee)
        if options_departement:
            departements = ["Tous les départements"] + options_departement
        else:
            # Pas encore de donnée "département" pour cette combinaison
            # Université/UFR (ex: UNB, UNZ, la plupart des Instituts...).
            # NOUVEAU (22/08/2026) : on affiche quand même "Tous les
            # départements" (comme Université/UFR), plutôt que "à
            # compléter" -- cohérent visuellement avec le reste des
            # filtres, et filtrer_mentors()/specialites_disponibles()
            # traitent déjà les deux valeurs de façon identique (aucun
            # filtre appliqué).
            departements = ["Tous les départements"]

        # Spécialité dépendant du département sélectionné -- 4e et
        # dernier niveau de la cascade (Université > UFR > Département >
        # Spécialité).
        try:
            departement_selectionne = input.departement()
        except Exception:
            departement_selectionne = "Tous les départements"
        if departement_selectionne not in departements:
            departement_selectionne = departements[0]

        options_specialite = specialites_disponibles(univ_selectionnee, ufr_selectionnee, departement_selectionne)
        specialites = ["Toutes les spécialités"] + options_specialite if options_specialite else ["Toutes les spécialités"]

        # UFR choisi pour les questions suggérées -- liste INDEPENDANTE de
        # l'université choisie plus haut (même sigle d'UFR = mêmes
        # questions, quelle que soit l'université qui l'offre).
        try:
            ufr_questions_choisi = input.ufr_questions_suggerees()
        except Exception:
            ufr_questions_choisi = "Choisir un UFR"

        return ui.TagList(
            ui.input_action_button(
                id="nouvelle_conversation",
                label=ui.HTML("<span style='font-size:1.05rem;'>+</span> Nouveau"),
            ),
            ui.h4("Filtrer par", style="margin-top: 0; margin-bottom: 2px;"),
            ui.div(
                EXPLICATION_FILTRES,
                style="font-size: 0.72rem; color: #8a8a8a; margin-bottom: 4px;",
            ),
            ui.input_select(
                id="universite",
                label="Université",
                choices=["Toutes les universités"] + list(UFR_PAR_UNIVERSITE.keys()),
                selected=univ_selectionnee,
            ),
            ui.input_select(id="ufr", label="UFR / Institut", choices=ufrs, selected=ufr_selectionnee),
            ui.input_select(id="departement", label="Département", choices=departements, selected=departement_selectionne),
            ui.input_select(id="specialite", label="Spécialité (optionnel)", choices=specialites),
            ui.input_action_button(id="rechercher", label="Rechercher", class_="btn-primary w-100", style="margin-top: 8px;"),
            ui.input_action_button(
                id="voir_liste",
                label="📋 Voir la liste",
                class_="w-100",
                style="margin-top: 6px;",
            ),
            ui.h4("💬 Questions suggérées", style="margin-top: 14px; margin-bottom: 6px;"),
            ui.input_select(
                id="ufr_questions_suggerees",
                label=None,
                choices={"Choisir un UFR": "Choisir un UFR", **UFR_GLOBAUX},
                selected=ufr_questions_choisi,
            ),
            _bloc_questions_suggerees(ufr_questions_choisi),
            *bloc_historique,
            ui.input_action_button(
                "btn_infos",
                "ℹ️ Infos & communiqués",
                class_="w-100 btn-outline-secondary",
                style="margin-top: 6px;",
            ),
        )

    # -------------------------------------------------------------------
    # Fenêtre "Infos & communiqués" : regroupe dans une fenêtre à onglets
    # (au lieu de tout étaler dans la sidebar) les communiqués (activités
    # scientifiques, colloques...) et une présentation de l'application.
    # Pensé pour être EXTENSIBLE : tout nouvel ajout futur (FAQ, guide
    # d'utilisation, contact...) prend juste un onglet de plus ici, sans
    # jamais surcharger la sidebar elle-même.
    # -------------------------------------------------------------------
    @reactive.effect
    @reactive.event(input.btn_infos)
    def _():
        ui.modal_show(
            ui.modal(
                # Onglets construits en HTML Bootstrap "brut" (nav-tabs /
                # tab-content / data-bs-toggle) plutôt qu'avec ui.navset_tab :
                # Bootstrap est de toute façon déjà chargé par shiny (les
                # modales et les boutons en dépendent), donc ce balisage
                # standard fonctionne de façon fiable, quelle que soit la
                # version de shiny installée.
                ui.tags.ul(
                    ui.tags.li(
                        ui.tags.button(
                            "📢 Communiqués",
                            class_="nav-link active",
                            type="button",
                            role="tab",
                            **{"data-bs-toggle": "tab", "data-bs-target": "#mbf-tab-communiques"},
                        ),
                        class_="nav-item",
                        role="presentation",
                    ),
                    ui.tags.li(
                        ui.tags.button(
                            "ℹ️ À propos",
                            class_="nav-link",
                            type="button",
                            role="tab",
                            **{"data-bs-toggle": "tab", "data-bs-target": "#mbf-tab-apropos"},
                        ),
                        class_="nav-item",
                        role="presentation",
                    ),
                    class_="nav nav-tabs",
                ),
                ui.tags.div(
                    ui.tags.div(
                        ui.div(*_bloc_communiques()),
                        class_="tab-pane fade show active",
                        id="mbf-tab-communiques",
                        role="tabpanel",
                    ),
                    ui.tags.div(
                        ui.h5("MentorBF"),
                        ui.p(
                            "MentorBF aide les étudiants et futurs chercheurs du Burkina "
                            "Faso à trouver un encadreur académique -- par nom, par thème "
                            "de recherche, ou par UFR/département -- pour leurs mémoires "
                            "de licence, masters et thèses de doctorat."
                        ),
                        ui.p(
                            "Les données couvrent les universités publiques du Burkina "
                            "Faso (UJKZ, UNB, UNZ...), enrichies avec leurs publications "
                            "scientifiques quand elles sont disponibles.",
                            style="color:#555;",
                        ),
                        class_="tab-pane fade",
                        id="mbf-tab-apropos",
                        role="tabpanel",
                    ),
                    class_="tab-content",
                    style="margin-top: 14px;",
                ),
                title="MentorBF -- Informations",
                easy_close=True,
                size="l",
                footer=ui.modal_button("Fermer"),
            )
        )


# =========================================================================
# ZONE PRINCIPALE : formulaire de connexion OU chat, selon l'état de session
# =========================================================================
chat = ui.Chat(id="chat")


@render.ui
def zone_principale():
    if current_user() is None:
        # Nouveau state à chaque affichage de cet écran, même logique que
        # dans _construire_modal_etape1() (bouton Google dupliqué ici pour
        # plus de visibilité, comme sur claude.ai/ChatGPT -- pas besoin
        # d'ouvrir une modale pour voir l'option Google).
        bouton_google_accueil = None
        if _google_active():
            etat = secrets.token_urlsafe(24)
            google_oauth_state.set(etat)
            google_oauth_traite.set(False)
            bouton_google_accueil = ui.div(
                ui.tags.a(
                    ui.HTML(
                        '<svg width="18" height="18" viewBox="0 0 48 48" style="vertical-align:middle;margin-right:8px;">'
                        '<path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.6 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.7-.4-3.5z"/>'
                        '<path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.5 15.9 18.9 13 24 13c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.5 6.1 29.5 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>'
                        '<path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.5-5.2l-6.2-5.3C29.2 35.4 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.6 39.6 16.2 44 24 44z"/>'
                        '<path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.3-4.1 5.7l6.2 5.3C39.9 37 44 31 44 24c0-1.3-.1-2.7-.4-3.5z"/>'
                        '</svg>'
                    ),
                    "Continuer avec Google",
                    href=_url_connexion_google(etat),
                    id="mbf_btn_google_accueil",
                    class_="mbf-btn-google-accueil",
                ),
                style="margin-top: 14px;",
            )
        return ui.div(
            ui.img(src="logo_mentorbf.jpg", id="mbf_login_logo"),
            ui.h3("👋 Bienvenue sur MentorBF", id="mbf_login_titre"),
            ui.p(
                id="mbf_login_texte",
                data_text=(
                    "Un chatbot qui centralise les enseignants-chercheurs "
                    "burkinabè -- leurs domaines d'expertise, leurs "
                    "publications, leurs travaux de recherche. Connectez-vous "
                    "ou créez un compte pour explorer la base."
                ),
            ),
            ui.input_action_button(
                "btn_ouvrir_connexion_centre", "Connexion / Créer un compte",
                class_="mbf-btn-google-accueil",
            ),
            bouton_google_accueil,
            style="max-width: 480px; margin: 4rem auto; text-align: center;",
        )

    resultats = liste_resultats()
    bloc_liste = None
    if liste_active():
        if resultats:
            lignes = [
                ui.tags.tr(
                    ui.tags.td(m.get("name", "")),
                    ui.tags.td(m.get("universite_code", "")),
                    ui.tags.td(m.get("departement", "Information non disponible")),
                    ui.tags.td(m.get("specialite", "Information non disponible")),
                )
                for m in resultats
            ]
            corps = ui.tags.table(
                ui.tags.thead(ui.tags.tr(ui.tags.th("Nom"), ui.tags.th("Université"), ui.tags.th("Département"), ui.tags.th("Spécialité"))),
                ui.tags.tbody(*lignes),
                class_="table table-striped table-sm",
                style="margin-top: 8px;",
            )
        else:
            corps = ui.p(
                "Aucun résultat pour ces filtres -- soit il n'y a pas de "
                "fiche correspondante, soit la donnée département n'est "
                "pas encore disponible pour cette combinaison (à compléter).",
                style="margin-top: 8px; color: #777;",
            )

        bloc_liste = ui.div(
            ui.div(
                ui.h4(f"📋 {len(resultats)} résultat(s)"),
                ui.input_action_button("fermer_liste", "✕ Fermer", class_="btn-sm"),
                style="display:flex; justify-content:space-between; align-items:center;",
            ),
            corps,
            style="max-height: 45vh; overflow-y: auto; border: 1px solid #ddd; "
                  "border-radius: 8px; padding: 12px; margin-bottom: 12px;",
        )

    return ui.TagList(bloc_liste, chat.ui(messages=[], height="100%"))


# =========================================================================
# ZONE D'UPLOAD PDF/PHOTO -- fonction @render.ui TOP-LEVEL SÉPARÉE (même
# principe que zone_connexion_topright plus haut), volontairement PAS
# imbriquée dans zone_principale().
#
# BUG CORRIGÉ (20/08/2026) : quand ce bloc était construit à l'intérieur de
# zone_principale() (juste après chat.ui(...)), toute mise à jour de
# resume_upload() (ex: "⏳ Analyse en cours...", puis le résultat) invalidait
# TOUTE la fonction zone_principale() -- qui recréait alors un chat.ui(...)
# tout neuf, effaçant instantanément toute la conversation (y compris un
# message tout juste ajouté via chat.append_message()). D'où l'effet
# "le message apparaît puis disparaît aussitôt". En isolant ce bloc dans sa
# propre fonction @render.ui top-level, sa réactivité (resume_upload) ne
# touche plus jamais chat.ui.
# =========================================================================
@render.ui
def zone_upload_document():
    return None
    # DÉSACTIVÉ TEMPORAIREMENT (05/09/2026) -- bouton "Browse..." mal
    # stylé, à corriger plus tard. Décommenter les 2 lignes ci-dessous
    # pour réactiver :
    # if current_user() is None:
    #     return None
    # return _bloc_upload_document()




# =========================================================================
# OVERLAY DE CHARGEMENT (nouveau, 21/08/2026 -- v2) -- élément STATIQUE,
# toujours présent dans le HTML (caché par défaut via CSS), basculé par
# le handler JS "mbf_busy" (voir mbfSetup plus haut) sur réception d'un
# message custom envoyé par _busy_afficher()/_busy_masquer() ci-dessus.
# PAS un @render.ui : voir le commentaire détaillé au-dessus de
# _busy_afficher() pour pourquoi la version réactive ne s'affichait
# jamais à l'écran.
#
# IMPORTANT : appelé ici comme EXPRESSION top-level, SANS assignation --
# en Shiny Express, seule une expression "nue" (pas une affectation) est
# auto-affichée dans la page (transformation AST interne d'Express). Un
# `overlay_busy = ui.div(...)` ne serait JAMAIS rendu du tout.
# =========================================================================
ui.div(
    ui.div(class_="mbf-spinner"),
    ui.div("Chargement en cours...", class_="mbf-spinner-texte"),
    id="mbf_overlay_busy",
    style="display:none;",
)


# =========================================================================
# ÉCRAN D'ACCUEIL (nouveau, 22/08/2026 -- v2, corrigé) -- logo + texte de
# bienvenue, affiché uniquement TANT QU'AUCUN message n'a été envoyé,
# comme sur claude.ai : dès que l'étudiant envoie sa première question,
# cet écran disparaît en fondu et laisse place à la conversation normale.
#
# BUG CORRIGÉ : la première version était un élément STATIQUE (toujours
# dans le HTML, quel que soit l'état de connexion) -- il se superposait
# donc à l'écran de connexion ("👋 Bienvenue sur MentorBF" défini dans
# zone_principale()), créant un texte illisible mélangeant les deux
# écrans. Cette fonction @render.ui, comme zone_upload_document()
# juste au-dessus, ne retourne le hero QUE si un utilisateur est
# connecté -- les deux écrans ne peuvent donc plus jamais se superposer.
# Toujours PAS de reactive.Value pilotant sa visibilité une fois affiché
# (current_user() change rarement, contrairement à resume_upload/
# overlay_message) : le masquage au premier message reste géré côté
# client (JS, voir mbfSetup) pour la même raison que pour l'overlay de
# chargement -- réactivité Shiny trop lente/peu fiable pour un fondu
# précis pendant la conversation.
# =========================================================================
@render.ui
def zone_hero_accueil():
    if current_user() is None:
        return None
    return ui.div(
        ui.img(src="logo_mentorbf.jpg", id="mbf_hero_logo"),
        ui.div("MentorBF", id="mbf_hero_titre"),
        ui.div(MESSAGE_ACCUEIL, id="mbf_hero_texte"),
        id="mbf_hero_accueil",
    )



def _bloc_upload_document():
    """Zone d'envoi de document (PDF/photo, uniquement pour les
    utilisateurs connectés). Appelée depuis zone_upload_document()
    ci-dessus, une fonction @render.ui top-level SÉPARÉE de
    zone_principale() -- justement pour que les mises à jour de
    resume_upload() n'invalident QUE ce petit bloc, jamais le chat (voir
    le commentaire détaillé au-dessus de zone_upload_document()).

    Le document n'est JAMAIS envoyé comme message dans le chat lui-même
    quand ce n'est pas un article scientifique -- dans ce cas seulement,
    c'est une notice affichée ici. Si c'est un article scientifique, le
    résumé est plutôt posté DANS la conversation (voir le handler
    d'upload plus bas) et resume_upload() reste vide."""
    texte_resume = resume_upload()
    bloc_resume = (
        ui.p(texte_resume, style="color:#444; font-size:0.9em; margin-top:4px; max-width:280px;")
        if texte_resume else None
    )
    return ui.div(
        ui.input_file(
            "upload_document",
            None,  # pas de label au-dessus -- compact, juste le bouton
            accept=[".pdf", ".jpg", ".jpeg", ".png"],
            multiple=False,
           # button_label="📎Ajouter un PDF/photo",
            placeholder="",  # remplace "No file selected" -- pas utile ici
            width="auto",
        ),
        bloc_resume,
        id="mbf_upload_zone",
    )


def _extraire_texte_pdf(chemin: str, max_pages: int = 3) -> str:
    """Extrait le texte des premières pages d'un PDF (suffisant pour
    couvrir titre + résumé/abstract d'un article scientifique, sans
    charger tout le document)."""
    lecteur = PdfReader(chemin)
    textes = []
    for page in lecteur.pages[:max_pages]:
        try:
            textes.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(textes)


@reactive.effect
@reactive.event(input.upload_document)
async def _():
    if current_user() is None:
        return
    fichiers = input.upload_document()
    if not fichiers:
        return

    info = fichiers[0]
    chemin = info["datapath"]
    nom_fichier = info["name"]

    # Overlay bien visible (spinner centré) pendant toute l'analyse --
    # voir _busy_afficher() plus haut. resume_upload garde son rôle pour
    # les messages d'erreur/notice près du bouton, mais le spinner est ce
    # qui donne le vrai feedback "quelque chose se passe".
    await _busy_afficher("Analyse du document en cours...")
    resume_upload.set(None)

    try:
        if nom_fichier.lower().endswith(".pdf"):
            type_fichier = "pdf"
            texte = _extraire_texte_pdf(chemin)
            if not texte.strip():
                resume_upload.set(
                    "⚠️ Impossible d'extraire du texte de ce PDF (peut-être un scan/image sans texte)."
                )
                return
            reponse = await resume_client.chat_async(texte[:6000], echo="none")
        else:
            type_fichier = "image"
            reponse = await resume_client.chat_async(
                "Voici une image envoyée par l'utilisateur.",
                ctl.content_image_file(chemin),
                echo="none",
            )

        # NB (bugfix) : `reponse` est un ChatResponseAsync -- contrairement à
        # ChatResponse (version synchrone), cette classe ne définit pas
        # __str__, donc str(reponse) affichait le repr Python brut de l'objet
        # (ex: "<chatlas._chat.ChatResponseAsync object at 0x...>") au lieu
        # du texte généré. Le texte consommé est déjà dans .content.
        resume_texte = reponse.content.strip()
        # NOUVEAU (05/09/2026) : protège les underscores des formules
        # (voir _proteger_underscores_maths en tête de fichier) avant que
        # ce résumé ne soit affiché dans le chat.
        resume_texte = _proteger_underscores_maths(resume_texte)

        # Si c'est bien un article scientifique, le prompt de resume_client
        # termine la réponse par une ligne MBF_SUGGESTIONS::[...] (voir plus
        # haut). Dans ce cas, on affiche le résultat dans la CONVERSATION,
        # comme une vraie réponse du chat -- avec les questions suggérées
        # cliquables en dessous, exactement via le même mécanisme JS que pour
        # les fiches enseignants (processSuggestions() plus haut dans le
        # fichier) -- pas dans la petite zone sous le bouton d'upload.
        est_article = "MBF_SUGGESTIONS::" in resume_texte

        if est_article:
            resume_upload.set(None)
            # Une seule icône (le 📄 dans le titre "### 📄 Résumé de ton
            # document" produit par resume_client) -- pas besoin de
            # surcharger AUSSI l'avatar du message avec une icône custom,
            # la coloration CSS (.mbf-doc-summary-message) suffit déjà à
            # le distinguer visuellement d'une réponse normale du chatbot.
            await chat.append_message({"content": resume_texte, "role": "assistant"})

            # On garde le contenu exploitable pour les questions de suivi
            # (voir _repondre() plus bas). Pour un PDF, c'est le texte
            # extrait déjà envoyé à resume_client (texte[:6000]) -- pour
            # une photo, on n'a pas de texte OCR séparé, donc on garde le
            # résumé produit (sans les lignes techniques MBF_DOC_SUMMARY /
            # MBF_SUGGESTIONS ni le titre Markdown, retirés ici pour ne pas
            # les répéter dans le contexte envoyé à chat_client ensuite).
            if type_fichier == "pdf":
                contenu_pour_suivi = texte[:6000]
            else:
                contenu_pour_suivi = (
                    resume_texte.split("MBF_SUGGESTIONS::")[0]
                    .replace("MBF_DOC_SUMMARY::true", "")
                    .replace("### 📄 Résumé de ton document", "")
                    .strip()
                )

            contexte_document.set({
                "nom_fichier": nom_fichier,
                "type": type_fichier,
                "contenu": contenu_pour_suivi,
            })
        else:
            # Pas un article scientifique : simple notice discrète près du
            # bouton, pas besoin d'encombrer la conversation avec ça.
            resume_upload.set(resume_texte)

        auth.add_document(current_user(), nom_fichier, type_fichier, resume_texte)

    except Exception as err:
        resume_upload.set(f"⚠️ Erreur lors de l'analyse du document : {err}")

    finally:
        # Toujours refermer l'overlay, même en cas d'erreur -- sinon le
        # spinner resterait affiché indéfiniment.
        await _busy_masquer()


def _code_universite(label: str):
    """'UJKZ - Université Joseph KI-ZERBO' -> 'UJKZ' (None si pas de filtre)"""
    if not label or label == "Toutes les universités":
        return None
    return label.split(" - ")[0].strip()


async def _repondre(user_input: str, univ_label: str, ufr: str, afficher_overlay: bool = True):
    """Fonction commune : utilisée par le chat ET par le bouton Rechercher.

    afficher_overlay : notre spinner centré maison (voir _busy_afficher())
    n'est utile QUE pour les déclencheurs qui n'ont AUCUN retour visuel
    natif -- le bouton "Rechercher" et les questions suggérées de la
    sidebar. Pour une question tapée/renvoyée directement dans le chat
    (chat.on_user_submit, crayon, bulle suggérée sous une réponse),
    shinychat affiche déjà nativement un spinner sur le bouton d'envoi
    PUIS les trois points "..." pendant la génération -- notre overlay y
    serait redondant (et donnait l'impression que "ça tourne" deux fois
    de suite avant même que la réponse commence)."""
    if current_user() is None:
        # Sécurité : ne devrait pas arriver puisque le chat n'est visible
        # qu'une fois connecté, mais on se protège quand même.
        return

    code_univ = _code_universite(univ_label)

    # Overlay bien visible (spinner centré) pendant la recherche RAG --
    # uniquement pour les déclencheurs sans retour visuel natif (voir
    # docstring ci-dessus).
    if afficher_overlay:
        await _busy_afficher("Recherche en cours...")

    try:
        fiches_pertinentes = build_context(user_input, top_k=5, universite_code=code_univ)

        # NOUVEAU (21/08/2026) : si l'étudiant a envoyé un article
        # scientifique (PDF/photo) plus tôt dans la session, on donne son
        # contenu à chat_client ici -- SANS ça, il ne pourrait jamais
        # répondre aux questions de suivi sur cet article (ex: questions
        # suggérées par resume_client juste après l'upload), puisqu'il
        # n'a normalement accès qu'aux fiches mentors du RAG (voir
        # prompt.txt, section dédiée qui explique explicitement la
        # distinction entre les deux cas à chat_client, pour ne pas
        # qu'il confonde avec l'interdiction sur les publications
        # listées dans une fiche mentor, où lui n'a jamais que
        # titre/annee/liens).
        doc = contexte_document()
        bloc_document = ""
        if doc:
            bloc_document = rf"""
        --- DOCUMENT ENVOYE PAR L'ETUDIANT (fichier : {doc['nom_fichier']}) ---
        Ceci n'est PAS une publication de la base mentors -- c'est un document
        que l'etudiant a lui-meme envoye a l'application. Voici son contenu
        (extrait, tel qu'extrait brut du PDF -- toute formule y apparait
        probablement entre simples parentheses ou crochets, ce qui est un
        artefact de l'extraction, PAS le format a reproduire) :

        {doc['contenu']}
        --- FIN DU DOCUMENT ENVOYE PAR L'ETUDIANT ---

        RAPPEL FORMULES : si tu reprends une formule de ce document dans ta
        reponse, ne recopie JAMAIS son parenthesage brut tel quel --
        reecris-la toujours en LaTeX propre avec $ ... $ (en ligne) ou
        $$ ... $$ (en bloc), comme demande dans tes instructions. Exemple :
        "( H_0 : r(x) = c )" du document doit devenir $H_0 : r(x) = c$
        dans ta reponse.
        """

        contexte = f"""
        Voici des fiches documentaires trouvées dans la base de donnees,
        deja filtrees selon l'universite choisie par l'etudiant si applicable.
        Utilise UNIQUEMENT ces informations (et le document envoyé par
        l'étudiant ci-dessous, s'il y en a un) pour repondre ; si elles ne
        suffisent pas, dis "Information non disponible".

        {fiches_pertinentes}
        {bloc_document}
        Filtres actifs :
        - Universite : {univ_label}
        - UFR : {ufr}

        Question de l'etudiant : {user_input}
        """

        response = await chat_client.stream_async(contexte)
    finally:
        # L'overlay ne sert qu'à couvrir l'attente "invisible" (RAG +
        # premier appel au modèle) -- une fois le flux de réponse obtenu,
        # le streaming dans le chat prend le relais comme retour visuel,
        # donc on referme l'overlay ici, AVANT d'afficher la réponse
        # (et systématiquement, même en cas d'erreur, pour ne jamais
        # laisser le spinner bloqué à l'écran).
        if afficher_overlay:
            await _busy_masquer()

    # On capture chaque morceau de la réponse au fil du streaming, pour
    # pouvoir sauvegarder la réponse complète dans l'historique à la fin.
    # NOTE : si chatlas renvoie des objets (pas de simples str) dans le
    # flux, adapter la ligne `morceaux.append(chunk)` en conséquence
    # (ex: chunk.content au lieu de chunk).
    morceaux = []

    async def _capturer(flux):
        # NOUVEAU (05/09/2026) : on bufferise jusqu'à ce qu'aucune formule
        # ne soit encore "ouverte" (voir _tampon_est_equilibre), puis on
        # protège les underscores des zones maths (voir
        # _proteger_underscores_maths) avant de transmettre le morceau au
        # chat -- sans ça, markdown casse les formules contenant _ (voir
        # le commentaire détaillé en tête de fichier).
        #
        # FILET DE SÉCURITÉ (05/09/2026) : si un $ isolé n'est JAMAIS
        # refermé (faute du modèle, ou simple "$" de prix/devise sans
        # rapport avec des maths), le tampon ne redevenait plus JAMAIS
        # "équilibré" -- tout le reste de la réponse restait bloqué,
        # invisible, jusqu'à la toute fin du flux (un seul gros paquet
        # d'un coup, ou rien du tout si le flux s'interrompait avant).
        # Au-delà de TAILLE_MAX_TAMPON caractères sans jamais retomber
        # équilibré, on force l'affichage quand même -- au pire une
        # formule mal composée dans un cas rarissime, plutôt que de
        # bloquer tout l'affichage du reste de la réponse.
        TAILLE_MAX_TAMPON = 400
        tampon = ""
        async for chunk in flux:
            tampon += chunk
            if _tampon_est_equilibre(tampon) or len(tampon) > TAILLE_MAX_TAMPON:
                corrige = _proteger_underscores_maths(tampon)
                morceaux.append(corrige)
                yield corrige
                tampon = ""
        if tampon:
            corrige = _proteger_underscores_maths(tampon)
            morceaux.append(corrige)
            yield corrige

    await chat.append_message_stream(_capturer(response))

    reponse_complete = "".join(morceaux)
    auth.add_history(current_user(), user_input, reponse_complete)
    historique_version.set(historique_version() + 1)


# =========================================================================
# QUESTIONS SUGGÉRÉES : un handler par bouton, enregistré une seule fois
# au démarrage (id statiques question_suggeree_0, _1... -- voir
# _bloc_questions_suggerees ci-dessus). On passe par une fonction "usine"
# (_faire_handler_question_suggeree) pour capturer correctement `texte`
# à chaque itération de la boucle -- sans ça, un piège classique de
# fermeture Python ferait que TOUS les handlers utiliseraient la
# DERNIÈRE valeur de `texte` de la boucle, pas la leur.
# =========================================================================
def _faire_handler_question_suggeree(index: int, texte: str):
    @reactive.effect
    @reactive.event(getattr(input, f"question_suggeree_{index}"))
    async def _handler():
        if current_user() is None:
            return
        univ = input.universite()
        try:
            ufr = input.ufr()
        except Exception:
            ufr = "Toutes les UFR"
        await _repondre(texte, univ, ufr)
    return _handler


# On garde une référence à chaque handler (variable module-level) --
# sinon rien n'empêche le ramasse-miettes de les libérer, ce qui
# désactiverait silencieusement les boutons.
_handlers_questions_suggerees = [
    _faire_handler_question_suggeree(i, question)
    for i, (sigle, question) in enumerate(QUESTIONS_SUGGEREES_APLATIES)
]


@chat.on_user_submit
async def _(user_input: str):
    univ = input.universite()
    try:
        ufr = input.ufr()
    except Exception:
        ufr = "Toutes les UFR"
    await _repondre(user_input, univ, ufr, afficher_overlay=False)


# =========================================================================
# QUESTION ENVOYÉE HORS DE LA ZONE DE SAISIE STANDARD. Ce même handler sert
# à DEUX déclencheurs JS différents (voir mbfSetup plus haut), qui envoient
# tous les deux via Shiny.setInputValue("mbf_edited_question", {text, ts}) :
#   1. Le crayon ✏️ sous une question de l'utilisateur ("Enregistrer et
#      renvoyer" après édition).
#   2. Une bulle de question suggérée 💬 sous une réponse du chat (générée
#      par le modèle via la ligne technique MBF_SUGGESTIONS::[...], voir
#      prompt.txt et processSuggestions() dans le JS).
# Dans les deux cas, le texte n'a pas transité par la zone de saisie
# standard, donc Shiny ne l'a pas affiché tout seul -- on l'ajoute
# nous-mêmes au chat, puis on relance la même logique de réponse.
# =========================================================================
@reactive.effect
@reactive.event(input.mbf_edited_question)
async def _():
    if current_user() is None:
        return
    payload = input.mbf_edited_question() or {}
    texte = (payload.get("text") or "").strip()
    if not texte:
        return

    univ = input.universite()
    try:
        ufr = input.ufr()
    except Exception:
        ufr = "Toutes les UFR"

    await chat.append_message({"content": texte, "role": "user"})
    await _repondre(texte, univ, ufr, afficher_overlay=False)


@reactive.effect
@reactive.event(input.rechercher)
async def _():
    if current_user() is None:
        return
    univ = input.universite()
    try:
        ufr = input.ufr()
    except Exception:
        ufr = "Toutes les UFR"

    question_auto = f"Liste des enseignants et professeurs de {ufr} a {univ}"
    await _repondre(question_auto, univ, ufr)


@reactive.effect
@reactive.event(input.voir_liste)
async def _():
    """Liste consultable directe (sans IA) : filtre mentors.json avec les
    filtres actifs de la sidebar et affiche le résultat tel quel, dans
    la zone principale. Rapide, gratuit, et fidèle aux données --
    contrairement au bouton 'Rechercher', qui passe par le RAG et l'IA
    et peut résumer/reformuler."""
    if current_user() is None:
        return
    univ = input.universite()
    try:
        ufr = input.ufr()
    except Exception:
        ufr = "Toutes les UFR"
    try:
        departement = input.departement()
    except Exception:
        departement = "Tous les départements"
    try:
        specialite = input.specialite()
    except Exception:
        specialite = "Toutes les spécialités"

    # Overlay bien visible (spinner centré), même si ce filtrage est très
    # rapide (local, sans IA) -- send_custom_message() (voir
    # _busy_afficher()) part immédiatement sur le websocket, donc pas
    # besoin d'astuce pour laisser le temps à l'overlay de s'afficher.
    await _busy_afficher("Recherche en cours...")

    try:
        liste_resultats.set(filtrer_mentors(univ, ufr, departement, specialite))
        liste_active.set(True)
    finally:
        await _busy_masquer()


@reactive.effect
@reactive.event(input.fermer_liste)
def _():
    liste_resultats.set([])
    liste_active.set(False)


# ---- "Nouvelle conversation" réinitialise le chat (sans toucher à l'historique enregistré) ----
@reactive.effect
@reactive.event(input.nouvelle_conversation)
async def _():
    await chat.clear_messages()
    # Le message d'accueil n'est plus un vrai message de chat (voir
    # #mbf_hero_accueil, écran statique séparé, masqué automatiquement
    # au premier message envoyé) -- on le fait donc réapparaître via un
    # message custom au lieu de le réinjecter dans l'historique.
    await session.send_custom_message("mbf_hero", {"show": True})


# =========================================================================
# HISTORIQUE (22/08/2026 -- v2) -- select compact (comme Université/UFR)
# + deux petits boutons "▶ Lancer" / "🗑 Supprimer" qui agissent sur
# l'entrée actuellement choisie dans historique_select. Boutons STATIQUES
# (mêmes id à chaque re-rendu de sidebar_contenu(), comme "rechercher" ou
# "voir_liste" juste au-dessus) -- pas besoin du mécanisme JS générique
# utilisé pour les questions suggérées, puisqu'il n'y a qu'UN SEUL select
# ici, pas une entrée par recherche.
# =========================================================================
@reactive.effect
@reactive.event(input.btn_historique_lancer)
async def _():
    if current_user() is None:
        return
    try:
        entry_id = int(input.historique_select())
    except (TypeError, ValueError):
        return

    entree = auth.get_history_entry(current_user(), entry_id)
    if entree is None:
        return

    # NOUVEAU (22/08/2026 -- v3, corrigé) : essayer de "rejouer" l'ancienne
    # réponse déjà terminée (via append_message() simple, puis via un flux
    # à un seul morceau) laissait le chat bloqué indéfiniment sur son
    # indicateur "..." dans les deux cas -- cause exacte non identifiée
    # sans débogage en direct. Solution robuste retenue à la place : on
    # RELANCE la MÊME question à travers le vrai circuit (RAG + IA),
    # exactement comme le fait déjà une question suggérée cliquée ou le
    # crayon "renvoyer" -- un chemin déjà éprouvé et fiable partout
    # ailleurs dans l'appli. Contrepartie : la réponse est fraîchement
    # regénérée (probablement très proche de l'ancienne, mais pas
    # forcément identique au mot près), au lieu d'un simple rejeu figé.
    univ = input.universite()
    try:
        ufr = input.ufr()
    except Exception:
        ufr = "Toutes les UFR"

    await chat.clear_messages()
    await chat.append_message({"content": entree["query_text"], "role": "user"})
    await _repondre(entree["query_text"], univ, ufr, afficher_overlay=False)


@reactive.effect
@reactive.event(input.btn_historique_supprimer)
def _():
    if current_user() is None:
        return
    try:
        entry_id = int(input.historique_select())
    except (TypeError, ValueError):
        return
    auth.delete_history_entry(current_user(), entry_id)
    historique_version.set(historique_version() + 1)