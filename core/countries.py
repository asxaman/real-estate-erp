"""Countries for the Nationality dropdown, and a way to recognise what people type in an import."""
import re

# United Arab Emirates first (most tenants), then A to Z.
COUNTRIES = ["United Arab Emirates"] + sorted([
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda", "Argentina",
    "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados",
    "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina",
    "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia",
    "Cameroon", "Canada", "Central African Republic", "Chad", "Chile", "China", "Colombia", "Comoros",
    "Congo (Democratic Republic)", "Congo (Republic)", "Costa Rica", "Croatia", "Cuba", "Cyprus",
    "Czech Republic", "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador", "Egypt",
    "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji",
    "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana", "Greece", "Grenada",
    "Guatemala", "Guinea", "Guinea-Bissau", "Guyana", "Haiti", "Honduras", "Hong Kong", "Hungary",
    "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland", "Israel", "Italy", "Ivory Coast",
    "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati", "Kosovo", "Kuwait", "Kyrgyzstan",
    "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Marshall Islands",
    "Mauritania", "Mauritius", "Mexico", "Micronesia", "Moldova", "Monaco", "Mongolia", "Montenegro",
    "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand",
    "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan",
    "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland",
    "Portugal", "Qatar", "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe",
    "Saudi Arabia", "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia",
    "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain",
    "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan", "Tajikistan",
    "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga", "Trinidad and Tobago", "Tunisia",
    "Turkey", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine", "United Kingdom", "United States",
    "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia",
    "Zimbabwe",
])
CHOICES = [(name, name) for name in COUNTRIES]

# What people write instead of the country name: nationalities and short forms.
_ALIASES = {
    "Afghan": "Afghanistan", "Albanian": "Albania", "Algerian": "Algeria", "American": "United States",
    "Argentine": "Argentina", "Argentinian": "Argentina", "Armenian": "Armenia",
    "Australian": "Australia", "Austrian": "Austria", "Azerbaijani": "Azerbaijan",
    "Bahraini": "Bahrain", "Bangladeshi": "Bangladesh", "Belgian": "Belgium", "Brazilian": "Brazil",
    "British": "United Kingdom", "English": "United Kingdom", "Scottish": "United Kingdom",
    "Welsh": "United Kingdom", "Bulgarian": "Bulgaria", "Burmese": "Myanmar", "Cambodian": "Cambodia",
    "Cameroonian": "Cameroon", "Canadian": "Canada", "Chilean": "Chile", "Chinese": "China",
    "Colombian": "Colombia", "Congolese": "Congo (Democratic Republic)", "Croatian": "Croatia",
    "Cuban": "Cuba", "Cypriot": "Cyprus", "Czech": "Czech Republic", "Czechia": "Czech Republic",
    "Danish": "Denmark", "Dutch": "Netherlands", "Holland": "Netherlands", "Egyptian": "Egypt",
    "Emirati": "United Arab Emirates", "UAE": "United Arab Emirates", "U.A.E": "United Arab Emirates",
    "Eritrean": "Eritrea", "Ethiopian": "Ethiopia", "Filipino": "Philippines", "Filipina": "Philippines",
    "Finnish": "Finland", "French": "France", "Georgian": "Georgia", "German": "Germany",
    "Ghanaian": "Ghana", "Greek": "Greece", "Hungarian": "Hungary", "Icelandic": "Iceland",
    "Indian": "India", "Indonesian": "Indonesia", "Iranian": "Iran", "Iraqi": "Iraq", "Irish": "Ireland",
    "Israeli": "Israel", "Italian": "Italy", "Ivorian": "Ivory Coast", "Jamaican": "Jamaica",
    "Japanese": "Japan", "Jordanian": "Jordan", "Kazakh": "Kazakhstan", "Kenyan": "Kenya",
    "Korean": "South Korea", "Kuwaiti": "Kuwait", "Kyrgyz": "Kyrgyzstan", "Lebanese": "Lebanon",
    "Libyan": "Libya", "Malaysian": "Malaysia", "Mauritanian": "Mauritania", "Mexican": "Mexico",
    "Moroccan": "Morocco", "Nepali": "Nepal", "Nepalese": "Nepal", "New Zealander": "New Zealand",
    "Nigerian": "Nigeria", "Norwegian": "Norway", "Omani": "Oman", "Pakistani": "Pakistan",
    "Palestinian": "Palestine", "Peruvian": "Peru", "Polish": "Poland", "Portuguese": "Portugal",
    "Qatari": "Qatar", "Romanian": "Romania", "Russian": "Russia", "Russian Federation": "Russia",
    "Rwandan": "Rwanda", "Saudi": "Saudi Arabia", "KSA": "Saudi Arabia", "Senegalese": "Senegal",
    "Serbian": "Serbia", "Singaporean": "Singapore", "Somali": "Somalia", "South African": "South Africa",
    "Spanish": "Spain", "Sri Lankan": "Sri Lanka", "Sudanese": "Sudan", "Swedish": "Sweden",
    "Swiss": "Switzerland", "Syrian": "Syria", "Taiwanese": "Taiwan", "Tanzanian": "Tanzania",
    "Thai": "Thailand", "Tunisian": "Tunisia", "Turkish": "Turkey", "Turkiye": "Turkey",
    "Ugandan": "Uganda", "Ukrainian": "Ukraine", "UK": "United Kingdom", "Great Britain": "United Kingdom",
    "England": "United Kingdom", "USA": "United States", "US": "United States",
    "United States of America": "United States", "Uzbek": "Uzbekistan", "Venezuelan": "Venezuela",
    "Vietnamese": "Vietnam", "Yemeni": "Yemen", "Zambian": "Zambia", "Zimbabwean": "Zimbabwe",
    "Burma": "Myanmar", "Congo": "Congo (Republic)", "Cote d'Ivoire": "Ivory Coast",
}


def _key(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


_LOOKUP = {_key(name): name for name in COUNTRIES}
_LOOKUP.update({_key(alias): country for alias, country in _ALIASES.items()})


def match(text):
    """'Egyptian', 'egypt', 'UAE', 'U.K.' ... -> the country name in the list, or '' if unknown."""
    return _LOOKUP.get(_key(text), "")
