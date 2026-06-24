import re
from datetime import datetime

MOIS_FR = [
    'janvier', 'février', 'mars', 'avril', 'mai', 'juin',
    'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'
]
MOIS_EN_ABBR = [
    'jan', 'feb', 'mar', 'apr', 'may', 'jun',
    'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
]

OUTPUT_FORMAT = '%d-%m-%Y %H:%M:%S'

def try_parse_iso_formats(raw: str) -> datetime:
    """Gère les priorités 2, 3, 4 et 5 (Formats ISO et dérivés non ambigus)."""
    # Priorité 2 : yyyy-MM-dd'T'HH:mm:ss Z / +TZ
    # Remplacement du 'Z' final par '+00:00' pour une meilleure compatibilité de parsing
    cleaned = raw.replace('Z', '+00:00')
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
            
    # Priorité 3 : yyyy-MM-dd HH:mm:ss
    # Priorité 4 : yyyy-MM-dd
    # Priorité 5 : yyyyMMdd (ISO compact)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None

def try_parse_textual_month(raw: str) -> datetime:
    """Priorité 6 : Détection des mois textuels FR ou EN (ex: 22 mars 2024, Mar 22 2024)."""
    val_lower = raw.lower()
    
    # 1. Test Mois Textuel Français (ex: 22 mars 2024)
    for i, mois in enumerate(MOIS_FR, start=1):
        if mois in val_lower:
            # Extraction basique des chiffres pour le jour et l'année
            nums = re.findall(r'\d+', raw)
            if len(nums) >= 2:
                jour, annee = int(nums[0]), int(nums[1])
                # Extraction optionnelle de l'heure si présente
                heure, minute, seconde = 0, 0, 0
                time_match = re.search(r'(\d{2}):(\d{2}):(\d{2})', raw)
                if time_match:
                    heure, minute, seconde = map(int, time_match.groups())
                return datetime(annee, i, jour, heure, minute, seconde)

    # 2. Test Mois Textuel Anglais Abgrégé (ex: Mar 22 2024)
    for i, mois in enumerate(MOIS_EN_ABBR, start=1):
        if mois in val_lower:
            nums = re.findall(r'\d+', raw)
            if len(nums) >= 2:
                # Dans le format US, le jour vient souvent après ou avant, on s'adapte
                jour, annee = int(nums[0]), int(nums[1])
                heure, minute, seconde = 0, 0, 0
                time_match = re.search(r'(\d{2}):(\d{2}):(\d{2})', raw)
                if time_match:
                    heure, minute, seconde = map(int, time_match.groups())
                return datetime(annee, i, jour, heure, minute, seconde)
                
    return None

def try_parse_ambiguous_formats(raw: str, hint: str) -> datetime:
    """Priorités 7 et 8 : Gestion des séparateurs standards (/ ou - ou .) avec application des règles d'ambiguïté."""
    # Nettoyage des séparateurs pour uniformiser en '/'
    normalized = raw.replace('-', '/').replace('.', '/')
    
    # Extraction des blocs numériques (ex: ['22', '03', '2024', '14', '30', '00'])
    parts = re.findall(r'\d+', normalized)
    if len(parts) < 3:
        return None
        
    p1, p2, p3 = int(parts[0]), int(parts[1]), int(parts[2])
    
    # Extraction de l'heure si elle existe (Priorité 8)
    h, m, s = 0, 0, 0
    if len(parts) >= 6:
        h, m, s = int(parts[3]), int(parts[4]), int(parts[5])

    # RÈGLE 1 : Jour > 12 -> Forcément au format DMY (Pas de mois 13+)
    if p1 > 12 and p2 <= 12:
        return datetime(p3, p2, p1, h, m, s)
    # Cas inverse (ex: 03/22/2024) -> Forcément MDY
    if p2 > 12 and p1 <= 12:
        return datetime(p3, p1, p2, h, m, s)

    # RÈGLE 3 : Jour <= 12 ET Mois <= 12 -> AMBIGU -> Application stricte du HINT
    if p1 <= 12 and p2 <= 12:
        if hint == 'DMY':
            return datetime(p3, p2, p1, h, m, s)
        elif hint == 'MDY':
            return datetime(p3, p1, p2, h, m, s)

    return None

def parse_date_cell(value: str, hint: str = 'DMY') -> str:
    """Retourne la date normalisée au format DD-MM-YYYY HH:mm:ss ou la valeur inchangée si non parsable."""
    # Règle Cellule vide / None / NaN
    if value is None or str(value).strip() == '' or str(value).lower() in ['nan', 'null', 'none']:
        return ""

    raw = str(value).strip()

    # --- Priorité 1 : Timestamp Unix entier ---
    if raw.isdigit() and len(raw) >= 10:
        try:
            ts = int(raw)
            # Différenciation secondes (10 chiffres) et millisecondes (13 chiffres)
            dt = datetime.fromtimestamp(ts / 1000) if ts > 5000000000 else datetime.fromtimestamp(ts)
            return dt.strftime(OUTPUT_FORMAT)
        except (ValueError, OverflowError):
            pass

    # --- Priorités 2, 3, 4, 5 : Formats ISO ---
    dt = try_parse_iso_formats(raw)
    if dt:
        return dt.strftime(OUTPUT_FORMAT)

    # --- Priorité 6 : Mois textuels ---
    dt = try_parse_textual_month(raw)
    if dt:
        return dt.strftime(OUTPUT_FORMAT)

    # --- Priorités 7 & 8 : Formats Ambigus (Slash/Tiret/Points) + Extraction heure ---
    dt = try_parse_ambiguous_formats(raw, hint)
    if dt:
        return dt.strftime(OUTPUT_FORMAT)

    # Règle Cellule invalide : retourner la valeur INCHANGÉE, sans exception bloquante
    return raw

def normalize_column(series, hint: str = 'DMY'):
    """Applique parse_date_cell sur toute une colonne pandas (Baseline naïve)."""
    return series.apply(lambda v: parse_date_cell(v, hint))
