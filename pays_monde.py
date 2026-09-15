"""
pays_monde.py -- liste des pays du monde (noms français), pour le menu
déroulant "Pays" du formulaire d'inscription MentorBF.

Burkina Faso est mis en tête de liste (choix le plus probable pour la
majorité des utilisateurs), suivi du reste des pays par ordre
alphabétique. Un choix "Préfère ne pas préciser" est ajouté en tout
premier, car ce champ est OPTIONNEL (l'idée du projet : une personne
peut être burkinabè vivant à l'étranger, ou une personne d'un autre pays
-- ce champ ne doit jamais donner l'impression d'être obligatoire).
"""

PAYS_MONDE = [
    "Préfère ne pas préciser",
    "Burkina Faso",
    "Afghanistan", "Afrique du Sud", "Albanie", "Algérie", "Allemagne",
    "Andorre", "Angola", "Antigua-et-Barbuda", "Arabie Saoudite",
    "Argentine", "Arménie", "Australie", "Autriche", "Azerbaïdjan",
    "Bahamas", "Bahreïn", "Bangladesh", "Barbade", "Belgique", "Belize",
    "Bénin", "Bhoutan", "Biélorussie", "Birmanie (Myanmar)", "Bolivie",
    "Bosnie-Herzégovine", "Botswana", "Brésil", "Brunei", "Bulgarie",
    "Burundi",
    "Cambodge", "Cameroun", "Canada", "Cap-Vert",
    "République centrafricaine", "Chili", "Chine", "Chypre", "Colombie",
    "Comores", "Congo (Brazzaville)", "Congo (RDC)", "Corée du Nord",
    "Corée du Sud", "Costa Rica", "Côte d'Ivoire", "Croatie", "Cuba",
    "Danemark", "Djibouti", "Dominique",
    "Égypte", "Émirats arabes unis", "Équateur", "Érythrée", "Espagne",
    "Estonie", "Eswatini (Swaziland)", "États-Unis", "Éthiopie",
    "Fidji", "Finlande", "France",
    "Gabon", "Gambie", "Géorgie", "Ghana", "Grèce", "Grenade",
    "Guatemala", "Guinée", "Guinée-Bissau", "Guinée équatoriale",
    "Guyana",
    "Haïti", "Honduras", "Hongrie",
    "Îles Marshall", "Îles Salomon", "Inde", "Indonésie", "Irak",
    "Iran", "Irlande", "Islande", "Israël", "Italie",
    "Jamaïque", "Japon", "Jordanie",
    "Kazakhstan", "Kenya", "Kirghizistan", "Kiribati", "Koweït",
    "Laos", "Lesotho", "Lettonie", "Liban", "Liberia", "Libye",
    "Liechtenstein", "Lituanie", "Luxembourg",
    "Macédoine du Nord", "Madagascar", "Malaisie", "Malawi", "Maldives",
    "Mali", "Malte", "Maroc", "Maurice", "Mauritanie", "Mexique",
    "Micronésie", "Moldavie", "Monaco", "Mongolie", "Monténégro",
    "Mozambique",
    "Namibie", "Nauru", "Népal", "Nicaragua", "Niger", "Nigeria",
    "Norvège", "Nouvelle-Zélande",
    "Oman", "Ouganda", "Ouzbékistan",
    "Pakistan", "Palaos", "Palestine", "Panama",
    "Papouasie-Nouvelle-Guinée", "Paraguay", "Pays-Bas", "Pérou",
    "Philippines", "Pologne", "Portugal",
    "Qatar",
    "République dominicaine", "République tchèque", "Roumanie",
    "Royaume-Uni", "Russie", "Rwanda",
    "Saint-Christophe-et-Niévès", "Saint-Marin",
    "Saint-Vincent-et-les-Grenadines", "Sainte-Lucie", "Salvador",
    "Samoa", "Sao Tomé-et-Principe", "Sénégal", "Serbie", "Seychelles",
    "Sierra Leone", "Singapour", "Slovaquie", "Slovénie", "Somalie",
    "Soudan", "Soudan du Sud", "Sri Lanka", "Suède", "Suisse",
    "Suriname", "Syrie",
    "Tadjikistan", "Tanzanie", "Tchad", "Thaïlande", "Timor oriental",
    "Togo", "Tonga", "Trinité-et-Tobago", "Tunisie", "Turkménistan",
    "Turquie", "Tuvalu",
    "Ukraine", "Uruguay",
    "Vanuatu", "Vatican", "Venezuela", "Viêt Nam",
    "Yémen",
    "Zambie", "Zimbabwe",
    "Autre / non listé",
]