"""
ETAPE : Extraire trois champs structurés et filtrables pour chaque
mentor, à partir du texte déjà scrapé :
  - "ufr"         : depuis la ligne "UFR:" (fiches ProGRES) ou
                    "Etablissement:" (fiches UNB) présente dans le texte
  - "specialite"  : la discipline précise scrapée ("Discipline:", "Domaine:"
                    ou "Spécialité:"), ex: "Cardiologie", "Algèbre"
  - "departement" : le VRAI département officiel, déduit UNIQUEMENT à
                    l'intérieur de l'UFR/institut confirmé du mentor
                    (jamais en mélangeant les UFR/instituts entre eux)

CORRECTION IMPORTANTE (20/08/2026) : ce qu'on appelle "département" dans
notre système correspond en réalité à ce que les sites d'orientation
officiels (oreilleducampus.org, MESRSI) appellent "FILIÈRE". Ce n'est PAS
une sous-division administrative interne à l'UFR (comme on le pensait au
départ avec l'exemple UFR/SEA -> Mathématiques/Physique/Chimie/
Informatique) -- c'est simplement le PROGRAMME/LA LICENCE proposée dans
cette UFR. Grâce au scraping automatisé d'oreilleducampus.org
(scraper_oreilleducampus.py -> structure_ufr_filieres.json), on connaît
maintenant la vraie liste de filières pour la quasi-totalité des UFR/
instituts de UJKZ, UNB et UNZ (les 3 universités pour lesquelles
mentors.json contient réellement des enseignants à ce jour) -- ce script
couvre donc BEAUCOUP plus de départements que la version précédente, qui
se limitait à UFR/SEA et ISSP faute de données.

Pour les 13 autres établissements publics identifiés (UTS, ULBO, UYAT,
UDOC, UV-BF, EPO, les CUP...), voir cartographie_universites_ufr_filieres.md
pour leurs filières connues -- non intégrées ici tant que mentors.json ne
contient aucun enseignant de ces établissements (inutile de définir des
départements pour des UFR sans aucune fiche à classer).

RÈGLE STRICTE POUR LES UFR ELLES-MÊMES : le département d'un mentor n'est
déduit QUE si on connaît avec certitude son UFR (champ "ufr" scrapé,
identifié avec >= 50% de recouvrement de mots avec un libellé connu). La
recherche par mots-clés se fait ENSUITE uniquement dans les départements
de CETTE UFR précise -- jamais dans une autre UFR de la même université,
et jamais dans une autre université (bug corrigé historiquement : un
mentor de l'UFR/SVT ne peut plus se voir attribuer un département de
Sociologie ou de Chirurgie).

CAS DES INSTITUTS (pas de sigle "UFR/...") : traités À PART de la logique
UFR_LIBELLES/identifier_ufr_code, dans DEPARTEMENTS_INSTITUTS -- IMPORTANT,
ce dict est maintenant imbriqué PAR UNIVERSITÉ (DEPARTEMENTS_INSTITUTS
[universite_code][sigle_institut]), car un même sigle d'institut peut
exister dans PLUSIEURS universités avec des filières complètement
différentes (ex: "IUT" existe à la fois à l'UNB et à l'UNZ, avec des
filières distinctes -- les confondre aurait proposé les filières de l'UNB
à un mentor de l'IUT de l'UNZ, et inversement).

DÉPARTEMENTS RESTANT "À COMPLÉTER" MALGRÉ DES DONNÉES DISPONIBLES :
  - UJKZ/UFR-SVT : le scraping a renvoyé une liste suspecte (probable
    erreur d'extraction sur le site ou la page) -- volontairement laissé
    "à compléter" plutôt que d'utiliser une donnée non fiable.
  - UNB/UFR-LSH, UYAT (tous établissements sauf ENSI-F) : filières non
    confirmées par le scraping, présentes seulement dans une cartographie
    manuelle antérieure -- pas assez fiables pour être utilisées ici.

Entrée : mentors.json
Sortie : mentors.json (mis à jour avec les champs "ufr", "departement", "specialite")
"""
import json
import re
import unicodedata

MENTORS_PATH = "mentors.json"

MOTIF_SPECIALITE = re.compile(r"(?:Discipline|Domaine|Spécialité)\s*:\s*(.+)")
MOTIF_UFR = re.compile(r"^UFR\s*:\s*(.+)$", re.MULTILINE)
MOTIF_ETABLISSEMENT = re.compile(r"^Etablissement\s*:\s*(.+)$", re.MULTILINE)
MOTIF_THEME_THESE = re.compile(r"^Th[eè]me de th[eè]se\s*:\s*(.+)$", re.MULTILINE)
# Ajouté le 20/08/2026 : repli pour l'UNB, qui n'a AUCUN champ "Discipline"
# côté source -- voir 10_scrape_unb.py/11_merge_unb.py.
MOTIF_LABORATOIRE = re.compile(r"^Laboratoire de rattachement\s*:\s*(.+)$", re.MULTILINE)


def _sans_accents(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode("ascii")
    return texte.lower()


def _mots_significatifs(texte: str) -> set:
    """Mots de plus de 3 lettres, en ignorant les mots trop génériques
    qui reviennent dans presque tous les libellés d'UFR ('Sciences'...)
    et fausseraient la comparaison floue."""
    mots_generiques = {"sciences", "science", "faculte", "unite", "recherche", "ufr", "universite"}
    return {
        w for w in re.findall(r"\w+", _sans_accents(texte))
        if len(w) > 3 and w not in mots_generiques
    }


# =========================================================================
# LIBELLÉS COMPLETS DE CHAQUE UFR (sigle "UFR/..." uniquement -- pas les
# instituts, voir DEPARTEMENTS_INSTITUTS plus bas) -- utilisés pour
# identifier avec confiance à quelle UFR appartient un mentor, à partir
# du texte scrapé. Mis à jour le 20/08/2026 avec la structure réellement
# confirmée (scraping + vérification manuelle croisée), qui diverge par
# endroits de la version précédente (notamment UNB, entièrement fausse
# avant cette mise à jour).
# =========================================================================
UFR_LIBELLES = {
    "UJKZ": {
        "UFR/SVT": "Sciences de la Vie et de la Terre",
        "UFR/SEA": "Sciences Exactes et Appliquées",
        "UFR/SH": "Sciences Humaines",
        "UFR/LAC": "Lettres, Arts et Communication",
        "UFR/SDS": "Sciences de la Santé",
    },
    "UNB": {
        # Structure ENTIÈREMENT REMPLACÉE le 20/08/2026 : la précédente
        # (UFR/ST, UFR/SJP, UFR/SEG) venait d'oreilleducampus.org, qui
        # s'est avéré INCOMPLET/OBSOLÈTE pour l'UNB. Structure confirmée
        # directement sur le site officiel univ-bobo.gov.bf (menu
        # "Etablissements" + fiches individuelles) :
        "UFR/SVT": "Sciences de la Vie et de la Terre",
        "UFR/SEA": "Sciences Exactes et Appliquées",
        "UFR/SH-LAM": "Sciences Humaines, Lettres, Arts et Média",
        "UFR/SJPEG": "Sciences Juridiques, Politiques, Économiques et de Gestion",
    },
    "UNZ": {
        "UFR/ST": "Sciences et Technologies",
        "UFR/SEG": "Sciences Économiques et de Gestion",
        "UFR/LSH": "Lettres et Sciences Humaines",
    },
    "UTS": {
        "UFR/SEG": "Sciences Économiques et de Gestion",
        "UFR/SJP": "Sciences Juridiques et Politiques",
        "UFR/ST": "Sciences et Techniques",
    },
    # Ajouté le 20/08/2026 : la sidebar (Mentors.py) proposait déjà ULBO/
    # UDOC/UYAT comme universités sélectionnables, mais leur organigramme
    # n'avait jamais été renseigné ici -- résultat, le menu Département
    # affichait "à compléter" pour tout le monde, même sans rapport avec
    # la présence ou non d'enseignants dans mentors.json (le menu
    # Département vient de CE dict, pas de la base -- voir
    # _filieres_connues() dans Mentors.py). Corrigé ici pour UDOC (seule
    # des 3 à avoir de vraies UFR "UFR/..." confirmées) -- ULBO et UYAT
    # utilisent uniquement des instituts/écoles, voir DEPARTEMENTS_INSTITUTS.
    "UDOC": {
        "UFR/SAT": "Sciences Appliquées et Technologies",
        "UFR/LSH": "Lettres et Sciences Humaines",
    },
    # UYAT (Fada) : UFR/SHLAC ajoutée le 20/08/2026, confirmée par 2
    # sources concordantes (AUF + article MESRSI 2020) -- les autres
    # établissements UYAT (ENSI-F, ISDD, I3S) restent des instituts, voir
    # DEPARTEMENTS_INSTITUTS.
    "UYAT": {
        "UFR/SHLAC": "Sciences Humaines, Lettres, Arts et Communication",
    },
}

# =========================================================================
# ORGANIGRAMME -- départements (= FILIÈRES officielles, voir note en tête
# de fichier) PAR UFR, JAMAIS mélangés entre UFR différentes ni entre
# universités. Chaque tuple (departement, mots_cles) : le département est
# retenu si un des mots-clés apparaît dans la spécialité scrapée du
# mentor (comparaison insensible aux accents/casse).
# =========================================================================
ORGANIGRAMME = {
    "UJKZ": {
        "UFR/SEA": [
            ("Mathématiques", [
                "algebre", "analyse reelle", "analyse fonctionnelle", "analyse complexe",
                "analyse harmonique", "analyse numerique", "analyse mathematique",
                "analyse et controle", "calcul variationnel", "probabilit", "statistique",
                "modelisation", "optimisation", "equations differentielles",
                "systeme dynamique", "systemes dynamiques", "dynam", "geometrie", "topologie",
                "mathemat", "theorie des nombres", "analyse",
            ]),
            ("Physique", [
                "physique", "astrophysique", "energie", "energetique", "thermique",
                "materiaux", "plasma", "nanoscience", "photonique", "signal",
                "matiere condensee", "biophysique",
            ]),
            ("Chimie", ["chimie", "chimique", "substances naturelles", "materiau"]),
            ("Informatique", [
                "informatique", "genie logiciel", "reseaux", "donnees",
                "intelligence artificielle", "numerique", "data science",
            ]),
        ],
        # UFR/SVT : structure officielle réelle (tronc commun confirmé dès
        # la toute première cartographie du projet), remplace la liste
        # suspecte remontée par le scraping. Mots-clés construits à partir
        # de 75 spécialités réelles observées (diagnostic_departements.py).
        "UFR/SVT": [
            ("Géologie", [
                "geologie", "geophysique", "hydrogeologie", "geosciences",
                "geomorphologie", "pedologie", "sciences du sol",
            ]),
            ("Biochimie-Microbiologie", [
                "biochimie", "microbiologie", "virologie", "bacteriologie",
                "biotechnologie", "enzymologie", "biologie moleculaire",
            ]),
            ("Biologie végétale et physiologie végétale", [
                "biologie vegetale", "physiologie vegetale", "botanique",
                "phytoecologie", "phytopathologie", "amelioration des plantes",
                "production vegetale", "ethnobotanique", "phycologie",
                "ravageurs", "cultures",
            ]),
            ("Biologie animale et physiologie animale", [
                "biologie animale", "physiologie animale", "entomologie",
                "ecologie animale", "biologie et ecologie animales", "zoologie",
                "parasitologie", "biotechnologie animale", "neuroscience",
                "electrophysiologie",
            ]),
            ("MQIA", [
                "technologie des denrees alimentaires", "agroalimentaire",
                "hygiene alimentaire", "nutrition humaine", "sciences des aliments",
                "toxicologie alimentaire", "qualite",
            ]),
        ],
        "UFR/SH": [
            ("Géographie", [
                "geographie", "environnement", "amenagement du territoire",
                "foncier", "ressources naturelles", "urbain", "urbanisme",
                "gestion des risques", "geomatique", "migration",
            ]),
            ("Histoire et Archéologie", ["histoire", "archeologie"]),
            ("Philosophie", ["philosophie", "philo"]),
            ("Psychologie", ["psychologie"]),
            ("Sociologie", [
                "sociologie", "anthropologie", "socio-anthropologie",
                "sciences sociales",
            ]),
        ],
        "UFR/LAC": [
            ("Allemand", ["allemand", "germanique"]),
            ("Anglais", ["anglais", "anglophone", "civilisation americaine"]),
            ("Espagnol", ["espagnol"]),
            ("Lettres Modernes", [
                "lettres modernes", "litterature", "theatr", "etudes culturelles",
                "industries culturelles",
            ]),
            ("Linguistique", [
                "linguistique", "sciences du langage", "science du langage",
                "semiotique", "grammaire", "analyse du discours", "traductologie",
                "traduction",
            ]),
        ],
        "UFR/SDS": [
            # ORDRE IMPORTANT : Pharmacie et Psychiatrie vérifiées AVANT le
            # repli générique "Médecine" (sinon "pharmacologie" ou
            # "psychiatrie" seraient absorbées par Médecine en premier).
            ("Pharmacie", [
                "pharmacie", "pharmaceutique", "pharmacognosie", "phytochimie",
                "chimie therapeutique", "falsification", "medicament",
            ]),
            ("Psychiatrie", ["psychiatrie", "psychatrie"]),  # "psychatrie" : coquille récurrente dans les fiches sources
            ("TSS", ["technicien superieur de sante", "biomedicale", "analyse bio"]),
            # Médecine : repli large, couvre toutes les spécialités
            # médicales (pas de département séparé par spécialité côté
            # source officielle -- toutes rattachées à la même filière
            # Médecine).
            ("Médecine", [
                "medecine", "medical", "clinique", "chirurgie", "cardiologie",
                "pediatrie", "gynecologie", "obstetrique", "neurologie",
                "ophtalmologie", "dermatologie", "venerologie", "urologie",
                "andrologie", "oto-rhino", "orl", "anesthesie", "reanimation",
                "hepatologie", "gastro", "nephrologie", "orthopedie",
                "traumatologie", "radiologie", "radiodiagnostic", "imagerie",
                "bacteriologie", "parasitologie", "mycologie", "hematologie",
                "biochimie", "histologie", "embryologie", "cytogenetique",
                "anatomie", "physiologie", "epidemiologie", "sante publique",
                "maladies infectieuses", "pneumologie", "rhumatologie",
                "oncologie", "radiotherapie", "toxicologie", "geriatrie",
                "gerontologie", "cytologie pathologique", "sante au travail",
                "prothese", "parodontologie", "odontologie",
                "medecine traditionnelle", "sante et environnement",
            ]),
        ],
    },
    # UNB : construit le 20/08/2026 maintenant que le "Laboratoire de
    # rattachement" sert de source de spécialité (voir extraire_specialite
    # et MOTIF_LABORATOIRE). Mots-clés réutilisant le vocabulaire déjà
    # validé pour UJKZ/UNZ (les noms de laboratoires emploient le même
    # genre de terminologie disciplinaire) -- pas encore vérifiés sur de
    # vrais noms de laboratoires UNB variés (un seul exemple observé :
    # "Laboratoire d'Algèbre, de Mathématiques Discrètes et
    # d'Informatique"), à affiner si des erreurs de classement apparaissent.
    "UNB": {
        "UFR/SEA": [
            ("Mathématiques", [
                "algebre", "analyse", "mathemat", "probabilit", "statistique",
                "modelisation", "geometrie", "topologie", "equations differentielles",
            ]),
            ("Physique", ["physique", "energie", "energetique", "materiaux"]),
            ("Chimie", ["chimie", "chimique"]),
            ("Informatique", [
                "informatique", "reseaux", "donnees", "intelligence artificielle",
                "numerique", "logiciel",
            ]),
        ],
        "UFR/SVT": [
            ("Biochimie-Microbiologie", ["biochimie", "microbiologie", "biologie moleculaire"]),
            ("Biologie végétale et physiologie végétale", ["biologie vegetale", "botanique", "phytoecologie"]),
            ("Biologie animale et physiologie animale", [
                "biologie animale", "zoologie", "entomologie", "sante animale", "biotechnologies animales",
            ]),
            ("Géologie", ["geologie", "geophysique", "hydrogeologie", "sciences du sol"]),
        ],
        "UFR/SH-LAM": [
            ("Histoire et Archéologie", ["histoire", "archeologie"]),
            ("Sociologie", ["sociologie", "anthropologie"]),
            ("Lettres et Linguistique", ["lettres", "linguistique", "langue", "litterature"]),
            ("Arts et Médias", ["arts", "media", "communication", "audiovisuel"]),
        ],
        "UFR/SJPEG": [
            ("Droit", ["droit", "juridique"]),
            ("Sciences Politiques", ["science politique", "sciences politiques", "politique"]),
            ("Sciences économiques", ["economique", "economie"]),
            ("Gestion", ["gestion", "management"]),
        ],
    },
    "UNZ": {
        "UFR/SEG": [
            ("Analyse et politiques économiques", ["politique economique", "analyse economique"]),
            ("Économie agricole et de l'environnement", ["economie agricole", "environnement"]),
            ("Économie et Sciences de gestion", ["gestion", "management", "economie"]),
        ],
        "UFR/LSH": [
            ("Géographie", ["geographie"]),
            ("Lettres modernes", ["lettres modernes", "litterature"]),
            ("Psychologie", ["psychologie"]),
            ("Histoire et Archéologie", ["histoire", "archeologie"]),
            ("Philosophie", ["philosophie"]),
            ("Sciences de l'Information Documentaire", ["information documentaire", "documentation"]),
            # Élargi pour couvrir les enseignants sans "ufr" scrapé (voir
            # fonction _deduire_via_specialite_seule) -- Sociolinguistique,
            # Ethnolinguistique et Sciences du langage n'étaient rattachées
            # à aucun département avant.
            ("Sciences du langage", [
                "sciences du langage", "science du langage", "sociolinguistique",
                "ethnolinguistique", "linguistique",
            ]),
        ],
        "UFR/ST": [
            ("Mathématiques-Physique-Chimie et Informatique", [
                "mathematique", "mathemat", "physique", "chimie", "informatique",
                "algebre", "geophysique",
            ]),
            ("Sciences de la Vie et de la Terre", [
                "svt", "vie et de la terre", "biologie", "geologie", "ecologie vegetale",
            ]),
        ],
    },
    "UTS": {
        "UFR/SEG": [
            ("Sciences économiques", ["economique", "economie"]),
            ("Gestion", ["gestion", "management"]),
        ],
        "UFR/SJP": [
            ("Droit", ["droit", "juridique"]),
        ],
        "UFR/ST": [
            ("LIME", ["ingenierie mathematique economique", "lime"]),
            ("LISE", ["ingenierie statistique economique", "lise", "statistique economique"]),
            ("MPCI", ["mathematiques-physique-chimie-informatique", "mathematique", "physique", "chimie", "informatique"]),
        ],
    },
    "UDOC": {
        "UFR/SAT": [
            ("Management de la Qualité en Industrie Agroalimentaire", [
                "management de la qualite", "industrie agroalimentaire", "qualite",
            ]),
            ("Sciences Biologiques (Biologie Générale)", ["biologie generale"]),
            ("Sciences Biologiques (Biochimie-Microbiologie)", ["biochimie", "microbiologie"]),
        ],
        "UFR/LSH": [
            ("Lettres Modernes", ["lettres modernes", "litterature"]),
            ("Sociologie et Anthropologie", ["sociologie", "anthropologie"]),
        ],
    },
    "UYAT": {
        "UFR/SHLAC": [
            ("Histoire et Archéologie", ["histoire", "archeologie"]),
            ("Lettres Modernes", ["lettres modernes", "litterature"]),
        ],
    },
}

# =========================================================================
# INSTITUTS (pas de sigle "UFR/...") -- imbriqués PAR UNIVERSITÉ, car un
# même sigle d'institut (ex: "IUT") peut exister dans plusieurs
# universités avec des filières complètement différentes. Chaque entrée :
# DEPARTEMENTS_INSTITUTS[universite_code][sigle_institut] = [(departement,
# mots_cles), ...], et NOMS_INSTITUTS[universite_code][sigle_institut] =
# [motifs de reconnaissance texte].
# =========================================================================
NOMS_INSTITUTS = {
    "UJKZ": {
        "ISSP": ["issp", "institut superieur des sciences de la population"],
        "IBAM": ["ibam", "institut burkinabe des arts et metiers"],
        "IPERMIC": ["ipermic", "institut panafricain", "institut panafriacain"],  # "panafriacain" : coquille présente dans les fiches sources (lettres inversées)
        "IGEDD": ["igedd", "institut du genie de l'environnement", "institut de genie de l'environnement"],
        "ISSDH": ["issdh", "institut superieur du sport", "institut superieur des sciences du sport"],
        # CU Kaya / CU Ziniaré : campus décentralisés rattachés
        # administrativement à l'UJKZ (universite_code="UJKZ" dans
        # mentors.json), repérés lors du diagnostic du 20/08/2026 --
        # n'ont pas de sigle "UFR/...", traités comme des instituts.
        "CU Kaya": ["cu kaya", "centre universitaire de kaya"],
        "CU Ziniare": ["cu ziniare", "cu ziniaré", "centre universitaire de ziniare", "centre universitaire de ziniaré"],
        "IFOAD": ["ifoad", "formation ouverte"],  # motif court, evite les mots de liaison variables
    },
    "UNB": {
        # Structure ENTIÈREMENT REVUE le 20/08/2026 après consultation
        # directe du site officiel univ-bobo.gov.bf (menu "Etablissements").
        # Les valeurs réelles du champ "ufr" scrapé sont TRÈS courtes (juste
        # le sigle, ex: "IUT", "CUB" -- jamais le nom complet), d'où des
        # motifs courts privilégiés ici plutôt que des noms complets.
        "ESI": ["esi", "ecole superieure d'informatique"],
        "INSSA": ["inssa", "institut superieur des sciences de la sante"],
        "IUT": ["iut", "institut universitaire de technolog"],
        # Nouveaux (n'étaient pas du tout reconnus avant) :
        "CUB": ["cub", "centre universitaire de banfora"],
        "CUG": ["cug", "centre universitaire de gaoua"],
        "IC": ["institut confucius"],  # "ic" seul seul est trop court/ambigu pour un match sûr
        "IDR": ["idr", "institut du developpement rural"],
    },
    "UNZ": {
        "IUT": ["institut universitaire de technologie"],
        "ENS": ["ecole normale superieure"],
        # Ajouté le 20/08/2026 : même oubli que Kaya/Ziniaré à l'époque --
        # CU Manga est listée dans la sidebar (Mentors.py) mais absente
        # d'ici. Confirmé structure de l'UNZ (arrêté n°2018-343).
        "CU Manga": ["cu manga", "centre universitaire de manga"],
    },
    "UTS": {
        "IFOAD": ["ifoad", "formation ouverte"],  # motif court, evite les mots de liaison variables
        "IUFIC": ["iufic", "institut universitaire de formation initiale et continue"],
    },
    # Ajouté le 20/08/2026 : ULBO/UDOC/UYAT étaient déjà dans la sidebar
    # (Mentors.py) mais absents d'ici -- menu Département vide pour eux
    # jusqu'à cette correction.
    "ULBO": {
        "ESSAN": ["essan", "ecole superieure des sciences de la sante"],
        "ESCME": ["escme", "ifpm", "ecole superieure de commerce"],
        "ESST": ["esst", "ecole superieure des sciences et technolog"],
    },
    "UDOC": {
        "ISEDR": ["isedr", "institut des sciences de l'environnement et du developpement rural"],
        "IUP": ["iup", "institut universitaire professionnalis"],
    },
    "UYAT": {
        # Structure mise à jour le 20/08/2026 (2 sources concordantes :
        # AUF + article MESRSI 2020). UFR/SAT, IUP, UFR-SEG (cartographie
        # jamais re-confirmée) retirés ; ISDD et I3S ajoutés.
        "ENSI-F": ["ensi-f", "ecole nationale superieure d'ingenieurs", "esi", "ecole superieure d'ingenierie"],
        "ISDD": ["isdd", "institut superieur de developpement durable"],
        # I3S : établissement confirmé (legrandfrere.africa) mais sans
        # détail de départements encore trouvé -- reconnu ici (pour ne
        # jamais confondre avec un autre institut), mais SANS entrée dans
        # DEPARTEMENTS_INSTITUTS ci-dessous -> retombe honnêtement sur
        # "à compléter" plutôt que d'inventer des départements santé.
        "I3S": ["i3s", "institut superieur des sciences de la sante"],
    },
    # =====================================================================
    # AJOUTÉ LE 20/08/2026 : UV-BF, EPO et les 4 CUP (Banfora, Gaoua, Dori,
    # Tenkodogo) étaient déjà dans la sidebar (Mentors.py) mais TOTALEMENT
    # ABSENTS d'ici -- audit complet fait suite à un signalement (le bug
    # ULBO/ESST s'est reproduit à l'identique pour ces 6 établissements).
    # Les sigles utilisés ci-dessous DOIVENT correspondre EXACTEMENT à
    # ceux utilisés dans UFR_PAR_UNIVERSITE (Mentors.py), sinon la
    # correspondance UFR <-> département casse silencieusement.
    # =====================================================================
    "UV-BF": {
        "Sciences Fondamentales (Licence)": ["sciences fondamentales"],
        "Sciences Transversales (Licence)": ["sciences transversales"],
        "Sciences du Numérique (Licence)": ["sciences du numerique", "sciences numeriques"],
    },
    "EPO": {
        "Programme Sciences fondamentales": ["sciences fondamentales"],
        "Programme Sciences transversales": ["sciences transversales"],
        "Programme Sciences du numérique": ["sciences du numerique", "sciences numeriques"],
    },
    "CUP/B": {
        "CUP/B": ["cup/b", "centre universitaire polytechnique de banfora"],
    },
    "CUP/G": {
        "CUP/G": ["cup/g", "centre universitaire polytechnique de gaoua"],
    },
    "CUP/D": {
        "CUP/D": ["cup/d", "centre universitaire polytechnique de dori"],
    },
    "CUP/T": {
        "CUP/T": ["cup/t", "centre universitaire polytechnique de tenkodogo"],
    },
}

DEPARTEMENTS_INSTITUTS = {
    "UJKZ": {
        "ISSP": [
            # ORDRE IMPORTANT : Statistique vérifié AVANT Démographie (voir
            # note historique -- Démographie est la catégorie large par
            # défaut, Statistique la mention la plus spécifique).
            ("Statistique", [
                "statistique", "biostatistique", "econometrie", "data science",
                "analyse de donnees", "probabilit",
            ]),
            ("Démographie", [
                "demographie", "population", "fecondite", "mortalite", "migration",
                "nuptialite", "sante de la reproduction", "sante reproductive",
                "dynamique des populations",
            ]),
        ],
        "IBAM": [
            ("CCA", ["comptabilite", "controle", "audit"]),
            ("ABF", ["assurance", "banque", "finance"]),
            ("MG", ["marketing", "gestion commerciale"]),
            ("ADB", ["assistant de direction", "secretariat", "bilingue"]),
            # MIAGE : élargi pour couvrir les vraies spécialités observées
            # (diagnostic_departements.py) -- l'IBAM héberge en réalité des
            # profils informatique/mathématiques appliquées bien au-delà
            # du seul intitulé "MIAGE" au sens strict.
            ("MIAGE", [
                "informatique appliquee a la gestion", "miage", "informatique",
                "reseaux informatiques", "systeme d'information", "systemes d'information",
                "intelligence artificielle", "internet des objets", "capteurs",
                "web semantique", "numerique", "mathematique", "modelisation",
            ]),
        ],
        "IPERMIC": [
            # Vérifiées avant le repli générique "communication" (voir plus
            # bas) pour que la spécificité l'emporte quand elle est présente.
            ("Communication pour le développement", ["communication pour le developpement"]),
            ("Journalisme", ["journalisme"]),
            # Repli générique : la grande majorité des fiches ne précisent
            # que "Communication" tout court (sans plus de détail) --
            # rattachée par défaut à la filière la plus généraliste.
            ("Communication d'entreprise / Relation publique", [
                "communication d'entreprise", "relation publique", "communication",
            ]),
        ],
        "IGEDD": [
            ("Technologies de l'Eau et Assainissement", ["eau", "assainissement"]),
            ("Gestion des Pollutions et Aménagement du Territoire", [
                "pollution", "amenagement du territoire", "ecosysteme",
                "restauration", "rehabilitation",
            ]),
        ],
        "ISSDH": [
            ("STAPS", [
                "sport", "activite physique", "psychomotricite", "activites physiques",
                "gouvernance des organisations sportives", "politiques sportives",
            ]),
            ("STASE", [
                "socio-educ", "action sociale", "sociologie du developpement",
            ]),
        ],
        "CU Kaya": [
            ("Mathématiques et Physique", [
                "mathematique", "probabilit", "biomathematiques", "modelisation",
                "theorie de controle", "algebrique", "transfert", "physique",
                "semi-conducteurs", "semiconducteurs",
            ]),
            ("Sciences Biologiques et Agroalimentaire", [
                "physiologie animale", "nutrition", "technologie alimentaire",
                "toxicologie alimentaire", "biochimie", "sciences des aliments",
                "biotechnologie alimentaire",
            ]),
            ("Génie Civil", ["genie civil"]),
            ("Économie et Gestion", ["economie", "finance"]),
            ("Informatique", ["informatique"]),
            ("Psychologie", ["psychologie"]),
            ("Anglais", ["anglophone", "etudes anglophones"]),
        ],
        "CU Ziniare": [
            # Correspond aux filières officielles du CUP Ziniaré déjà
            # documentées (cartographie_universites_ufr_filieres.md).
            ("Aménagement et développement durable du territoire", [
                "gestion des ressources naturelles", "geomatique", "amenagement",
                "geographie urbaine", "gouvernance territoriale", "geomorphologie",
                "climat", "environnement",
            ]),
            ("Production et santé animale", [
                "physiologie animale", "zootechnie", "entomologie",
                "amelioration genetique",
            ]),
            ("Production végétale et agronomie", [
                "genetique et amelioration des plantes", "botanique",
                "phytoecologie", "agro-pedologie",
            ]),
            ("MQIA", [
                "antibioresistance", "hygiene alimentaire", "contaminants alimentaires",
            ]),
            ("Agroéconomie", ["agroeconomie", "economie de l'environnement"]),
            ("Chimie", ["chimie organique", "substances naturelles"]),
        ],
        "IFOAD": [
            # PSIA : élargi -- "Sciences Informatiques Appliquées" au sens
            # large, couvre l'informatique et les mathématiques appliquées
            # (spécialités réellement observées, diagnostic_departements.py).
            ("PSIA", [
                "sciences informatiques appliquees", "psia", "informatique",
                "analyse numerique", "edp", "traitement d'image",
                "mathematiques appliquees",
            ]),
        ],
    },
    "UNB": {
        # Activé le 20/08/2026 (voir extraire_specialite/MOTIF_LABORATOIRE) :
        # ces listes utilisent maintenant le "Laboratoire de rattachement"
        # comme source de spécialité, faute de champ "Discipline" natif
        # côté site source.
        "ESI": [
            ("Analyse et Programmation", ["analyse et programmation", "programmation", "developpement"]),
            ("Réseaux et Maintenance Informatique", ["reseau", "maintenance informatique"]),
            # Ajouté le 20/08/2026 : LAMDI (Laboratoire d'Algèbre, de
            # Mathématiques Discrètes et d'Informatique) regroupe en
            # pratique presque tout le corps enseignant de l'ESI observé --
            # trop générique pour être rattaché aux deux filières précises
            # ci-dessus, traité comme sa propre catégorie plutôt que de
            # deviner laquelle des deux s'applique.
            ("Informatique (recherche fondamentale)", [
                "algebre", "mathematiques discretes", "lamdi",
            ]),
        ],
        "INSSA": [
            ("Médecine", [
                "medecine", "medical", "clinique", "chirurgie",
                "pathogenes", "maladies emergentes", "infectieux",
            ]),
            ("Pharmacie", ["pharmacie", "pharmaceutique"]),
        ],
        "IUT": [
            ("Secrétariat de Direction", ["secretariat"]),
            ("Communication d'Entreprise", ["communication d'entreprise"]),
            ("Finances-Comptabilité et Gestion des Entreprises", [
                "finance", "comptabilite", "controle", "audit",
            ]),
            ("Marketing et Gestion Commerciale", ["marketing", "gestion commerciale"]),
            ("Assurance Banque et Finances", ["assurance", "banque"]),
            ("Génie Mécanique et Productique", [
                "genie mecanique", "productique", "materiaux", "heliophysique",
            ]),
            ("Génie Industriel et Maintenance", ["genie industriel", "maintenance"]),
            ("Génie Électrique (électrotechnique)", ["electrotechnique"]),
            ("Génie Électrique (électronique)", ["electronique"]),
            ("Génie Civil", ["genie civil"]),
        ],
        "IDR": [
            ("Agronomie", ["agronomie", "agricole", "production vegetale", "fertilite du sol", "fertilite"]),
            ("Élevage", ["elevage", "zootechnie", "production animale"]),
            ("Environnement et Développement Rural", ["environnement", "developpement rural", "foret"]),
        ],
        # CUG : premier signal réel obtenu le 20/08/2026 (laboratoire
        # "Bioressources, Agrosystèmes et Santé de l'Environnement").
        # Une seule catégorie pour l'instant, à enrichir si d'autres
        # laboratoires CUG apparaissent.
        "CUG": [
            ("Agroenvironnement", ["bioressources", "agrosystemes", "sante de l'environnement"]),
        ],
        # CUB : campus décentralisé, spécialisation exacte encore inconnue
        # (aucun exemple de laboratoire observé pour l'instant) -- pas de
        # liste de départements pour l'instant, "à compléter" en attendant
        # de vraies données.
        # IC (Institut Confucius) : programme linguistique unique
        # (chinois), pas vraiment subdivisable en "départements" -- laissé
        # sans liste également.
    },
    "UNZ": {
        "IUT": [
            ("Secrétariat / Assistant de Direction", ["secretariat", "assistant de direction"]),
            ("Finances Comptabilité", ["finance", "comptabilite"]),
            ("Tourisme et Hôtellerie", ["tourisme", "hotellerie", "hotel"]),
            ("Marketing et Gestion", ["marketing", "gestion commerciale"]),
            ("Management des entreprises Touristiques et Gestion des entreprises hôtelières", [
                "entreprises touristiques", "entreprises hotelieres",
            ]),
            ("Génie Civil Bâtiment", ["genie civil", "batiment"]),
            ("Génie Électrique", ["genie electrique", "electrotechnique", "electronique"]),
        ],
        "ENS": [
            ("CAP-CEG", ["cap-ceg", "cap ceg", "enseignement general"]),
            ("CAET", ["caet", "enseignement technique"]),
            ("CAPES", ["capes", "enseignement secondaire"]),
            ("CAPET", ["capet"]),
        ],
        # Ajouté le 20/08/2026 : CU Manga confirmée structure de l'UNZ
        # (arrêté n°2018-343/MESRSI/CAB/SG). Niveau Licence uniquement --
        # les 3 offres Master (Socio-anthropologie, Agronomie, Production
        # animale) sont regroupées avec leurs équivalents Licence plutôt
        # que traitées comme des départements séparés.
        "CU Manga": [
            ("Agro-sylvopastoral", ["agro-sylvopastoral", "agro sylvopastoral", "sylvopastoral", "agronomie"]),
            ("Horticulture", ["horticulture"]),
            ("Socio-Anthropologie philosophie", [
                "socio-anthropologie", "anthropologie", "philosophie", "sociologie", "production animale",
            ]),
        ],
    },
    "UTS": {
        "IFOAD": [
            ("MOA", ["management des ong", "ong et des associations", "moa"]),
            ("MIBE", ["commerce international", "global marketing", "internationalisation", "mibe"]),
            ("PSIA", ["sciences informatiques appliquees", "psia"]),
        ],
        "IUFIC": [
            ("Sciences de Gestion", ["sciences de gestion", "gestion"]),
            ("Sciences Politiques", ["sciences politiques", "science politique"]),
            ("Management de Projets", ["management de projets", "gestion de projets"]),
            ("Santé Internationale", ["sante internationale"]),
            ("Génie de l'Environnement", ["genie de l'environnement"]),
            ("Droit et Politiques de l'Environnement", ["droit et politiques de l'environnement", "droit de l'environnement"]),
            ("Protection et Droits de l'Enfant", ["protection", "droits de l'enfant"]),
            ("Politiques de Développement et Gestion des Industries Extractives", [
                "industries extractives", "developpement et gestion",
            ]),
        ],
    },
    "ULBO": {
        "ESSAN": [
            ("Médecine", ["medecine", "medical", "clinique"]),
        ],
        "ESCME": [
            ("Finance Comptabilité", ["finance", "comptabilite"]),
            ("Marketing et Gestion commerciale", ["marketing", "gestion commerciale"]),
            ("AAA - Anglais Appliqué aux Affaires", ["anglais applique", "anglais des affaires"]),
            ("SAPE - Statistiques Appliquées et Prospectives Économiques", [
                "statistiques appliquees", "prospectives economiques",
            ]),
            ("GRH - Gestion des Ressources Humaines", ["gestion des ressources humaines", "grh"]),
            ("GFI - Gestion Foncière et Immobilière", ["gestion fonciere", "immobiliere", "gfi"]),
        ],
        "ESST": [
            ("MTHF - Mathématiques Fondamentales", ["mathematiques fondamentales", "mthf"]),
            ("MSN - Modélisation et Simulation Numérique", ["modelisation", "simulation numerique", "msn"]),
            ("Physiques", ["physique"]),
            ("Techniques environnementale et développement durable", [
                "techniques environnementale", "developpement durable",
            ]),
            ("TSID - Techniques Statistiques et Informatiques Décisionnelles", [
                "techniques statistiques", "informatiques decisionnelles", "tsid",
            ]),
        ],
    },
    "UDOC": {
        "ISEDR": [
            ("Agronomie", ["agronomie", "agricole"]),
            ("Élevage", ["elevage", "zootechnie"]),
            ("Eaux, Forêts et Environnement", ["eaux", "forets", "environnement"]),
        ],
        "IUP": [
            ("CCA - Comptabilité-Contrôle-Audit", ["comptabilite", "controle", "audit"]),
            ("MIAGE", ["informatique appliquee a la gestion", "miage"]),
        ],
    },
    "UYAT": {
        "ENSI-F": [
            ("Génie Civil (Bâtiments & Travaux Publics)", ["genie civil", "batiments", "travaux publics"]),
            ("Génie Géologique & Minier (Exploration & Exploitation Minières)", [
                "genie geologique", "genie minier", "exploration miniere", "exploitation miniere",
            ]),
        ],
        "ISDD": [
            ("Génie Agronomie", ["genie agronomie", "agronomie"]),
            ("Génie Élevage", ["genie elevage", "elevage"]),
            ("Génie Foresterie", ["genie foresterie", "foresterie", "forestier"]),
        ],
        # I3S : volontairement absent -- voir NOMS_INSTITUTS pour l'explication.
    },
    # =====================================================================
    # AJOUTÉ LE 20/08/2026 : UV-BF, EPO, CUP/B, CUP/G, CUP/D, CUP/T --
    # voir le commentaire d'avertissement dans NOMS_INSTITUTS ci-dessus,
    # même correction, même raison (audit suite à signalement du bug
    # ULBO/ESST reproduit à l'identique ici).
    # =====================================================================
    "UV-BF": {
        "Sciences Fondamentales (Licence)": [
            ("Mathématiques", ["mathematique", "modelisation", "calcul scientifique", "algebre"]),
            ("Physique", ["physique", "energie", "energetique", "renouvelable"]),
            ("Chimie", ["chimie"]),
            ("Sciences de la Vie et de la Terre", ["hygiene", "securite alimentaire", "svt"]),
        ],
        "Sciences Transversales (Licence)": [
            ("Géomatique", ["geomatique"]),
            ("Lettres Modernes", ["lettres modernes"]),
            ("Économie (SEG)", ["economie", "gestion"]),
            ("Droit (SJP)", ["droit", "juridique"]),
        ],
        "Sciences du Numérique (Licence)": [
            ("Génie Logiciel", ["genie logiciel", "pure developer", "developpeur"]),
        ],
    },
    "EPO": {
        "Programme Sciences fondamentales": [
            ("Mathématiques", ["mathematique", "modelisation", "calcul scientifique", "algebre"]),
            ("Physique", ["physique", "energie", "energetique", "renouvelable"]),
            ("Chimie", ["chimie"]),
            ("Sciences de la Vie et de la Terre", ["hygiene", "securite alimentaire", "svt"]),
        ],
        "Programme Sciences transversales": [
            ("Géomatique", ["geomatique"]),
        ],
        "Programme Sciences du numérique": [
            ("Génie Logiciel", ["genie logiciel", "pure developer", "developpeur"]),
        ],
    },
    "CUP/B": {
        "CUP/B": [
            ("Sciences Juridiques et Politiques", ["droit", "juridique", "politique"]),
        ],
    },
    "CUP/G": {
        "CUP/G": [
            ("Gestion du Patrimoine Culturel et Touristique", ["patrimoine culturel", "touristique", "tourisme"]),
            ("Sciences Biologiques et Appliquées", ["sciences biologiques", "biologie"]),
        ],
    },
    "CUP/D": {
        "CUP/D": [
            ("Mine et Carrière", ["mine", "carriere"]),
            ("Élevage", ["elevage"]),
        ],
    },
    "CUP/T": {
        "CUP/T": [
            ("Élevage et Aquaculture", ["elevage", "aquaculture"]),
            ("Agroéconomie", ["agroeconomie"]),
            ("Agricole et Forestière", ["agronomie", "environnement", "agricole", "forestiere"]),
        ],
    },
}


def _identifier_institut(ufr_texte: str, universite_code: str):
    """Repère si le texte d'établissement scrapé correspond à un institut
    connu DE CETTE UNIVERSITÉ PRÉCISE (jamais d'une autre -- un "IUT"
    scrapé pour un mentor UNZ ne doit jamais utiliser les filières de
    l'IUT de l'UNB, et inversement). Renvoie le sigle de l'institut ou
    None si aucun institut connu de cette université ne correspond."""
    if not ufr_texte or ufr_texte == "Information non disponible":
        return None
    noms = NOMS_INSTITUTS.get(universite_code)
    if not noms:
        return None
    texte_normalise = _sans_accents(ufr_texte)
    for sigle, motifs in noms.items():
        if any(motif in texte_normalise for motif in motifs):
            return sigle
    return None


def _normaliser(valeur: str) -> str:
    valeur = (valeur or "").strip()
    valeur_normalisee = valeur.lower()
    if not valeur or valeur_normalisee.startswith("information non disponible") or valeur_normalisee == "indisponible":
        return "Information non disponible"
    return valeur


def extraire_specialite(texte: str) -> str:
    m = MOTIF_SPECIALITE.search(texte or "")
    if m:
        return _normaliser(m.group(1))
    # Repli "Laboratoire de rattachement" (UNB uniquement) : utilisé
    # SEULEMENT quand aucune "Discipline:"/"Domaine:"/"Spécialité:" n'a
    # été trouvée -- ne remplace jamais une vraie donnée existante
    # ailleurs, sert uniquement là où le champ d'origine est totalement
    # absent (voir docstring en tête de fichier).
    m2 = MOTIF_LABORATOIRE.search(texte or "")
    if m2:
        return _normaliser(m2.group(1))
    return "Information non disponible"


def extraire_theme_these(texte: str) -> str:
    m = MOTIF_THEME_THESE.search(texte or "")
    return _normaliser(m.group(1)) if m else "Information non disponible"


def extraire_ufr(texte: str) -> str:
    """Cherche 'UFR:' (fiches ProGRES) en priorité, sinon 'Etablissement:'
    (fiches UNB, qui joue le même rôle structurel)."""
    m = MOTIF_UFR.search(texte or "")
    if m:
        return _normaliser(m.group(1))
    m2 = MOTIF_ETABLISSEMENT.search(texte or "")
    if m2:
        return _normaliser(m2.group(1))
    return "Information non disponible"


def _est_un_institut(ufr_texte: str) -> bool:
    if not ufr_texte or ufr_texte == "Information non disponible":
        return False
    return not ufr_texte.strip().lower().startswith("ufr")


def identifier_ufr_code(ufr_texte: str, universite_code: str):
    """Détermine avec CONFIANCE à quelle UFR (code officiel, ex: 'UFR/SEA')
    appartient le mentor. Renvoie None si aucune correspondance
    suffisamment forte (>= 50% de recouvrement de mots) -- dans ce cas on
    ne devine PAS le département ensuite."""
    libelles = UFR_LIBELLES.get(universite_code)
    if not libelles or not ufr_texte or ufr_texte == "Information non disponible":
        return None

    mots_texte = set(re.findall(r"\w+", _sans_accents(ufr_texte)))
    meilleur_code, meilleur_score = None, 0.0
    for code, libelle in libelles.items():
        sigle = code.split("/")[-1].strip().lower()
        if sigle and sigle in mots_texte:
            return code
        # Sigle composé avec tiret (ex: "SH-LAM") : le regex \w+ découpe le
        # tiret, donc "sh-lam" ne sera jamais un token unique dans
        # mots_texte -- on vérifie alors que CHAQUE partie du sigle est
        # présente séparément (match précis, pas une simple sous-chaîne
        # qui risquerait des faux positifs sur des sigles courts comme "st").
        if "-" in sigle:
            parties = [p for p in sigle.split("-") if p]
            if parties and all(p in mots_texte for p in parties):
                return code
        mots_libelle = _mots_significatifs(libelle)
        if not mots_libelle:
            continue
        recouvrement = mots_libelle & mots_texte
        score = len(recouvrement) / len(mots_libelle)
        if score > meilleur_score:
            meilleur_score, meilleur_code = score, code
    return meilleur_code if meilleur_score >= 0.5 else None


def _deduire_via_toutes_ufr(specialite: str, universite_code: str):
    """Repli pour les mentors dont l'UFR n'est PAS connue (ufr =
    'Information non disponible' -- notamment les "Professeurs
    Titulaires" scrapés depuis la page d'accueil de l'UNZ par
    12_scrape_unz.py, qui ne capture que nom+discipline, jamais l'UFR :
    trou structurel de cette source, pas un manque de mots-clés).

    Cherche le mot-clé dans TOUTES les UFR/instituts connus de cette
    université, mais n'accepte le résultat QUE s'il est UNIQUE (un seul
    département correspond dans toute l'université) -- en cas
    d'ambiguïté (plusieurs départements différents matchent), on reste
    prudent et on renvoie None ("à compléter") plutôt que de deviner au
    hasard. Ne s'applique QUE quand l'UFR est inconnue -- dès qu'elle est
    connue, la recherche stricte par UFR ci-dessus reste seule utilisée."""
    specialite_normalisee = _sans_accents(specialite)
    correspondances = set()

    for departements in ORGANIGRAMME.get(universite_code, {}).values():
        for departement, mots_cles in departements:
            if any(_sans_accents(mot) in specialite_normalisee for mot in mots_cles):
                correspondances.add(departement)

    for departements in DEPARTEMENTS_INSTITUTS.get(universite_code, {}).values():
        for departement, mots_cles in departements:
            if any(_sans_accents(mot) in specialite_normalisee for mot in mots_cles):
                correspondances.add(departement)

    if len(correspondances) == 1:
        return correspondances.pop()
    return None


def deduire_departement(specialite: str, universite_code: str, ufr_texte: str, theme_these: str = "Information non disponible") -> str:
    """Département (= filière officielle) déduit UNIQUEMENT à l'intérieur
    de l'UFR/institut confirmé du mentor, DANS SA PROPRE UNIVERSITÉ. 'à
    compléter' dans tous les cas où on n'a pas une certitude suffisante.

    Pour les INSTITUTS, on élargit la recherche de mots-clés à la
    spécialité ET au thème de thèse (theme_these), utile quand le champ
    "specialite" scrapé est trop court (ex: juste "Démographie" côté
    ProGRES) pour distinguer entre plusieurs départements possibles."""
    if specialite == "Information non disponible":
        return "Information non disponible"
    if not ufr_texte or ufr_texte == "Information non disponible":
        # UFR inconnue : dernier recours, voir _deduire_via_toutes_ufr.
        depuis_specialite_seule = _deduire_via_toutes_ufr(specialite, universite_code)
        return depuis_specialite_seule if depuis_specialite_seule else "à compléter"

    sigle_institut = _identifier_institut(ufr_texte, universite_code)
    if sigle_institut:
        departements_institut = DEPARTEMENTS_INSTITUTS.get(universite_code, {}).get(sigle_institut)
        if not departements_institut:
            return "à compléter"
        texte_recherche = specialite
        if theme_these and theme_these != "Information non disponible":
            texte_recherche += " " + theme_these
        texte_recherche_normalise = _sans_accents(texte_recherche)
        for departement, mots_cles in departements_institut:
            if any(_sans_accents(mot) in texte_recherche_normalise for mot in mots_cles):
                return departement
        return "à compléter"

    if _est_un_institut(ufr_texte):
        return "à compléter"

    ufr_code = identifier_ufr_code(ufr_texte, universite_code)
    if not ufr_code:
        return "à compléter"

    departements_de_cette_ufr = ORGANIGRAMME.get(universite_code, {}).get(ufr_code)
    if not departements_de_cette_ufr:
        return "à compléter"

    specialite_normalisee = _sans_accents(specialite)
    for departement, mots_cles in departements_de_cette_ufr:
        if any(_sans_accents(mot) in specialite_normalisee for mot in mots_cles):
            return departement
    return "à compléter"


def main():
    with open(MENTORS_PATH, encoding="utf-8") as f:
        mentors = json.load(f)

    for m in mentors:
        specialite = extraire_specialite(m.get("text", ""))
        ufr = extraire_ufr(m.get("text", ""))
        theme_these = extraire_theme_these(m.get("text", ""))
        m["specialite"] = specialite
        m["ufr"] = ufr
        m["departement"] = deduire_departement(specialite, m.get("universite_code"), ufr, theme_these)

    with open(MENTORS_PATH, "w", encoding="utf-8") as f:
        json.dump(mentors, f, ensure_ascii=False, indent=2)

    trouves_spec = sum(1 for m in mentors if m["specialite"] != "Information non disponible")
    trouves_dep = sum(1 for m in mentors if m["departement"] not in ("Information non disponible", "à compléter"))
    a_completer = sum(1 for m in mentors if m["departement"] == "à compléter")
    trouves_ufr = sum(1 for m in mentors if m["ufr"] != "Information non disponible")

    print(f"{trouves_spec}/{len(mentors)} fiches ont une spécialité identifiée.")
    print(f"{trouves_ufr}/{len(mentors)} fiches ont une UFR/établissement identifié.")
    print(f"{trouves_dep}/{len(mentors)} fiches classées dans un département (= filière) officiel confirmé.")
    print(f"{a_completer} fiches 'à compléter'.")

    from collections import Counter
    compte_dep = Counter(m["departement"] for m in mentors if m["departement"] not in ("Information non disponible", "à compléter"))
    print("\nRépartition par département officiel :")
    for valeur, nb in compte_dep.most_common(60):
        print(f"  {nb:4d}  {valeur}")


if __name__ == "__main__":
    main()