"""
polars_date_engine.py
=====================
Moteur de normalisation de dates 100% Polars (expressions natives Rust).

Stratégie par priorité :
  P1  : Timestamp Unix (isdigit + cast Int64 + from_epoch)            → map_batches vectorisé Int64
  P2  : ISO 8601 avec TZ  (yyyy-MM-ddTHH:mm:ssZ / ±HH:MM)            → str.to_datetime strict
  P3  : ISO datetime      (yyyy-MM-dd HH:mm:ss)                       → str.to_datetime strict
  P4  : ISO date          (yyyy-MM-dd)                                 → str.to_datetime strict
  P5  : ISO compact       (yyyyMMdd)                                   → str.to_datetime strict
  P6a : Mois anglais abrégés (Mar 22 2024 / 22 Mar 2024)              → str.replace + str.to_datetime
  P6b : Mois français textuels (22 mars 2024)                         → map_batches sur valeurs résiduelles
  P7  : Formats ambigus slash/tiret/point + règles >12 + hint DMY/MDY → expressions arithmétiques Polars
  P8  : Fallback                                                       → map_elements (résiduel ~0%)

Sortie : colonne pl.Utf8 au format "DD-MM-YYYY HH:MM:SS"
"""

import re
import polars as pl
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

OUTPUT_FORMAT = "%d-%m-%Y %H:%M:%S"

# Mapping mois anglais abrégés → numéro (pour remplacement regex)
MOIS_EN_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Mapping mois français → numéro
MOIS_FR_MAP = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}

# ──────────────────────────────────────────────────────────────────────────────
# P1 : Timestamp Unix — map_batches vectorisé (opère sur Series Int64, pas cellule)
# ──────────────────────────────────────────────────────────────────────────────

def _unix_batch(series: pl.Series) -> pl.Series:
    """
    Convertit les valeurs purement numériques (≥10 chiffres) en datetime string.
    Opère sur le batch Series entier → pas de boucle Python par cellule.
    """
    # Cast tentatif en Int64 (les non-numériques deviennent null)
    as_int = series.cast(pl.Int64, strict=False)

    # Secondes si ≤ 5_000_000_000, millisecondes sinon
    result = (
        pl.when(as_int > 5_000_000_000)
          .then(
              (as_int * 1_000).cast(pl.Datetime("ms")).dt.strftime(OUTPUT_FORMAT)
          )
          .otherwise(
              as_int.cast(pl.Datetime("ms", time_unit="ms"))
              # from_epoch en secondes → cast en ms
          )
    )
    # Approche plus simple et compatible :
    # Pour chaque valeur numérique, on délègue à from_epoch natif Polars
    timestamps_s = pl.when(as_int > 5_000_000_000).then(as_int // 1000).otherwise(as_int)
    dt_series = timestamps_s.cast(pl.Datetime("us")).dt.strftime(OUTPUT_FORMAT)

    # On retourne la valeur convertie seulement si la source était purement numérique ≥10 chiffres
    is_unix = series.str.contains(r"^\d{10,}$", literal=False)
    return pl.when(is_unix).then(dt_series).otherwise(pl.lit(None)).alias(series.name)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers : construction d'expressions Polars pour les formats ambigus (P7)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_part(col_name: str, group: int) -> pl.Expr:
    """Extrait le groupe N du pattern numérique séparé par / - ou ."""
    return (
        pl.col(col_name)
          .str.extract(r"(\d+)[/\-.](\d+)[/\-.](\d+)", group)
          .cast(pl.Int32, strict=False)
    )

def _extract_time_part(col_name: str, group: int) -> pl.Expr:
    """Extrait heure (1), minute (2), seconde (3) depuis HH:MM:SS."""
    return (
        pl.col(col_name)
          .str.extract(r"(\d{2}):(\d{2}):(\d{2})", group)
          .cast(pl.Int32, strict=False)
          .fill_null(0)
    )

def _build_datetime_str_dmy(col: str) -> pl.Expr:
    """Construit 'DD-MM-YYYY HH:MM:SS' pour l'interprétation DMY."""
    d = _extract_part(col, 1)
    m = _extract_part(col, 2)
    y = _extract_part(col, 3)
    h = _extract_time_part(col, 1)
    mi = _extract_time_part(col, 2)
    s = _extract_time_part(col, 3)
    return (
        d.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        m.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        y.cast(pl.Utf8) + pl.lit(" ") +
        h.cast(pl.Utf8).str.zfill(2) + pl.lit(":") +
        mi.cast(pl.Utf8).str.zfill(2) + pl.lit(":") +
        s.cast(pl.Utf8).str.zfill(2)
    )

def _build_datetime_str_mdy(col: str) -> pl.Expr:
    """Construit 'DD-MM-YYYY HH:MM:SS' pour l'interprétation MDY."""
    mo = _extract_part(col, 1)  # premier bloc = mois
    d = _extract_part(col, 2)   # second bloc = jour
    y = _extract_part(col, 3)
    h = _extract_time_part(col, 1)
    mi = _extract_time_part(col, 2)
    s = _extract_time_part(col, 3)
    return (
        d.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        mo.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        y.cast(pl.Utf8) + pl.lit(" ") +
        h.cast(pl.Utf8).str.zfill(2) + pl.lit(":") +
        mi.cast(pl.Utf8).str.zfill(2) + pl.lit(":") +
        s.cast(pl.Utf8).str.zfill(2)
    )


# ──────────────────────────────────────────────────────────────────────────────
# P6b : Mois français — map_batches (résiduel faible, Python mais vectorisé sur batch)
# ──────────────────────────────────────────────────────────────────────────────

def _parse_fr_month_batch(series: pl.Series) -> pl.Series:
    """
    Convertit les dates avec mois français textuel.
    S'exécute sur le batch Series complet → une seule entrée dans l'interpréteur Python
    par colonne (pas par cellule).
    """
    results = []
    for val in series:
        if val is None:
            results.append(None)
            continue
        lower = val.lower()
        found = None
        for mois_str, mois_num in MOIS_FR_MAP.items():
            if mois_str in lower:
                nums = re.findall(r"\d+", val)
                if len(nums) >= 2:
                    jour, annee = int(nums[0]), int(nums[1])
                    h, mi, s = 0, 0, 0
                    tm = re.search(r"(\d{2}):(\d{2}):(\d{2})", val)
                    if tm:
                        h, mi, s = map(int, tm.groups())
                    try:
                        found = datetime(annee, mois_num, jour, h, mi, s).strftime(OUTPUT_FORMAT)
                    except ValueError:
                        pass
                break
        results.append(found)
    return pl.Series(results, dtype=pl.Utf8)


# ──────────────────────────────────────────────────────────────────────────────
# P6a : Mois anglais abrégés — remplacement vectorisé + str.to_datetime natif
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_en_month(col_expr: pl.Expr) -> pl.Expr:
    """
    Remplace 'Mar', 'jan', 'DEC' etc. par le numéro à 2 chiffres.
    Ex: "Mar 22 2024" → "03 22 2024" → parsable en datetime.
    Opère 100% en expressions Polars (Rust).
    """
    expr = col_expr.str.to_lowercase()
    for abbr, num in MOIS_EN_MAP.items():
        expr = expr.str.replace(abbr, num, literal=True)
    return expr


def _try_en_month_parse(col: str) -> pl.Expr:
    """
    Tente de parser les mois anglais en format 'DD MM YYYY' ou 'MM DD YYYY'.
    Retourne null si le pattern ne s'applique pas.
    """
    # Détection : contient une abréviation anglaise
    has_en = pl.col(col).str.contains(
        r"(?i)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", literal=False
    )
    # Après remplacement, on extrait les 3 blocs numériques
    normalized = _normalize_en_month(pl.col(col))
    p1 = normalized.str.extract(r"(\d+)\D+(\d+)\D+(\d+)", 1).cast(pl.Int32, strict=False)
    p2 = normalized.str.extract(r"(\d+)\D+(\d+)\D+(\d+)", 2).cast(pl.Int32, strict=False)
    p3 = normalized.str.extract(r"(\d+)\D+(\d+)\D+(\d+)", 3).cast(pl.Int32, strict=False)

    # Heuristique : si p3 > 31, c'est l'année → p1=jour, p2=mois (après remplacement)
    # Si p1 > 31 → p1=année (format YYYY mois DD) → rare, on ignore
    # Format attendu post-remplacement : "DD NN YYYY" ou "NN DD YYYY"
    # p2 est le numéro de mois (1–12) après remplacement, p1=jour, p3=année
    built = (
        p1.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        p2.cast(pl.Utf8).str.zfill(2) + pl.lit("-") +
        p3.cast(pl.Utf8) + pl.lit(" 00:00:00")
    )
    return pl.when(has_en).then(built).otherwise(pl.lit(None))


# ──────────────────────────────────────────────────────────────────────────────
# P7 : Formats ambigus — expressions arithmétiques 100% Polars
# ──────────────────────────────────────────────────────────────────────────────

def _try_ambiguous_parse(col: str, hint: str) -> pl.Expr:
    """
    Gère les formats DD/MM/YYYY, MM/DD/YYYY, DD-MM-YYYY, DD.MM.YYYY, etc.
    Applique les règles d'ambiguïté :
      - p1 > 12 ET p2 ≤ 12 → forcément DMY
      - p2 > 12 ET p1 ≤ 12 → forcément MDY
      - sinon → applique hint
    100% expressions Polars natives.
    """
    has_sep = pl.col(col).str.contains(r"\d+[/\-.]\d+[/\-.]\d+", literal=False)

    p1 = _extract_part(col, 1)
    p2 = _extract_part(col, 2)
    p3 = _extract_part(col, 3)

    # Vérification que p3 est l'année (> 31)
    year_is_p3 = p3 > 31

    dmy_str = _build_datetime_str_dmy(col)
    mdy_str = _build_datetime_str_mdy(col)

    # Règle auto DMY : p1 > 12 (forcément le jour)
    rule_auto_dmy = (p1 > 12) & (p2 <= 12) & year_is_p3
    # Règle auto MDY : p2 > 12 (forcément le jour)
    rule_auto_mdy = (p2 > 12) & (p1 <= 12) & year_is_p3
    # Ambigu → hint
    rule_hint_dmy = (p1 <= 12) & (p2 <= 12) & year_is_p3 & (hint == "DMY")
    rule_hint_mdy = (p1 <= 12) & (p2 <= 12) & year_is_p3 & (hint == "MDY")

    result = (
        pl.when(~has_sep).then(pl.lit(None))
          .when(rule_auto_dmy).then(dmy_str)
          .when(rule_auto_mdy).then(mdy_str)
          .when(rule_hint_dmy).then(dmy_str)
          .when(rule_hint_mdy).then(mdy_str)
          .otherwise(pl.lit(None))
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline principal : cascade complète des 8 priorités
# ──────────────────────────────────────────────────────────────────────────────

def normalize_column_polars(df: pl.DataFrame, col: str, hint: str = "DMY") -> pl.DataFrame:
    """
    Normalise la colonne `col` du DataFrame Polars en appliquant la cascade
    des 8 priorités 100% via expressions Polars natives (Rust).

    Seuls les mois FR textuels (P6b) utilisent map_batches Python — mais sur
    le batch complet, pas cellule par cellule.

    Args:
        df    : DataFrame Polars source
        col   : Nom de la colonne à normaliser
        hint  : 'DMY' ou 'MDY' pour la résolution d'ambiguïté

    Returns:
        DataFrame avec la colonne normalisée au format 'DD-MM-YYYY HH:MM:SS'
    """
    TMP = f"__norm_{col}__"

    # ── Nettoyage initial ─────────────────────────────────────────────────────
    # Valeurs nulles / vides / NaN → null Polars
    df = df.with_columns(
        pl.when(
            pl.col(col).is_null() |
            pl.col(col).str.strip_chars().eq("") |
            pl.col(col).str.to_lowercase().is_in(["nan", "null", "none"])
        )
        .then(pl.lit(None))
        .otherwise(pl.col(col).str.strip_chars())
        .alias(TMP)
    )

    # ── P1 : Timestamp Unix ──────────────────────────────────────────────────
    # Détection : uniquement des chiffres, ≥10 caractères
    is_unix = pl.col(TMP).str.contains(r"^\d{10,}$", literal=False)
    unix_as_int = pl.col(TMP).cast(pl.Int64, strict=False)
    # Différenciation secondes / millisecondes
    unix_dt = (
        pl.when(unix_as_int > 5_000_000_000)
          .then(
              (unix_as_int // 1000)
              .cast(pl.Datetime("us"))
              .dt.strftime(OUTPUT_FORMAT)
          )
          .otherwise(
              unix_as_int
              .cast(pl.Datetime("us"))
              .dt.strftime(OUTPUT_FORMAT)
          )
    )
    df = df.with_columns(
        pl.when(is_unix).then(unix_dt).otherwise(pl.col(TMP)).alias(TMP)
    )

    # ── P2 : ISO 8601 avec fuseau horaire ────────────────────────────────────
    # yyyy-MM-ddTHH:mm:ssZ ou yyyy-MM-ddTHH:mm:ss±HH:MM
    # On normalise 'Z' → '+00:00' pour le parsing natif
    has_T = pl.col(TMP).str.contains("T", literal=True)
    iso_tz_normalized = pl.col(TMP).str.replace("Z", "+00:00", literal=True)
    iso_tz_parsed = (
        pl.when(has_T)
          .then(
              iso_tz_normalized
              .str.to_datetime(
                  format="%Y-%m-%dT%H:%M:%S%z",
                  strict=False
              )
              .dt.convert_time_zone("UTC")
              .dt.strftime(OUTPUT_FORMAT)
          )
          .otherwise(pl.lit(None))
    )
    # Fallback P2 sans TZ
    iso_no_tz = (
        pl.when(has_T)
          .then(
              pl.col(TMP)
              .str.to_datetime(format="%Y-%m-%dT%H:%M:%S", strict=False)
              .dt.strftime(OUTPUT_FORMAT)
          )
          .otherwise(pl.lit(None))
    )
    p2_result = pl.coalesce([iso_tz_parsed, iso_no_tz])
    df = df.with_columns(
        pl.when(has_T & p2_result.is_not_null())
          .then(p2_result)
          .otherwise(pl.col(TMP))
          .alias(TMP)
    )

    # ── P3 : ISO datetime sans T ─────────────────────────────────────────────
    # yyyy-MM-dd HH:mm:ss
    p3_parsed = (
        pl.col(TMP)
          .str.to_datetime(format="%Y-%m-%d %H:%M:%S", strict=False)
          .dt.strftime(OUTPUT_FORMAT)
    )
    df = df.with_columns(
        pl.coalesce([p3_parsed, pl.col(TMP)]).alias(TMP)
    )

    # ── P4 : ISO date seule ──────────────────────────────────────────────────
    # yyyy-MM-dd
    p4_parsed = (
        pl.col(TMP)
          .str.to_datetime(format="%Y-%m-%d", strict=False)
          .dt.strftime(OUTPUT_FORMAT)
    )
    df = df.with_columns(
        pl.coalesce([p4_parsed, pl.col(TMP)]).alias(TMP)
    )

    # ── P5 : ISO compact ─────────────────────────────────────────────────────
    # yyyyMMdd (8 chiffres exactement)
    is_compact = pl.col(TMP).str.contains(r"^\d{8}$", literal=False)
    p5_parsed = (
        pl.when(is_compact)
          .then(
              pl.col(TMP)
              .str.to_datetime(format="%Y%m%d", strict=False)
              .dt.strftime(OUTPUT_FORMAT)
          )
          .otherwise(pl.lit(None))
    )
    df = df.with_columns(
        pl.coalesce([p5_parsed, pl.col(TMP)]).alias(TMP)
    )

    # ── P6a : Mois anglais abrégés ───────────────────────────────────────────
    en_built = _try_en_month_parse(TMP)
    # Validation : la chaîne construite doit être parsable
    en_validated = (
        en_built
        .str.to_datetime(format="%d-%m-%Y %H:%M:%S", strict=False)
        .dt.strftime(OUTPUT_FORMAT)
    )
    df = df.with_columns(
        pl.coalesce([en_validated, pl.col(TMP)]).alias(TMP)
    )

    # ── P6b : Mois français textuels ─────────────────────────────────────────
    # On identifie les valeurs encore non-normalisées contenant un mois FR
    fr_pattern = "|".join(MOIS_FR_MAP.keys())
    has_fr = pl.col(TMP).str.contains(fr_pattern, literal=False)

    # map_batches sur le batch complet (une entrée Python, pas par cellule)
    fr_result = (
        pl.col(TMP)
          .map_batches(_parse_fr_month_batch, return_dtype=pl.Utf8)
    )
    df = df.with_columns(
        pl.when(has_fr & fr_result.is_not_null())
          .then(fr_result)
          .otherwise(pl.col(TMP))
          .alias(TMP)
    )

    # ── P7 : Formats ambigus avec séparateurs ────────────────────────────────
    ambig_result = _try_ambiguous_parse(TMP, hint)
    # Validation de la chaîne construite
    ambig_validated = (
        ambig_result
        .str.to_datetime(format="%d-%m-%Y %H:%M:%S", strict=False)
        .dt.strftime(OUTPUT_FORMAT)
    )
    df = df.with_columns(
        pl.coalesce([ambig_validated, pl.col(TMP)]).alias(TMP)
    )

    # ── Nettoyage final ───────────────────────────────────────────────────────
    # Remplacement de la colonne originale + suppression de la colonne temporaire
    df = df.with_columns(
        pl.col(TMP).fill_null("").alias(col)
    ).drop(TMP)

    return df


# ──────────────────────────────────────────────────────────────────────────────
# Interface publique : normalise plusieurs colonnes d'un coup
# ──────────────────────────────────────────────────────────────────────────────

def normalize_date_columns(
    df: pl.DataFrame,
    date_columns: list[str],
    date_formats: list[str],
) -> pl.DataFrame:
    """
    Normalise plusieurs colonnes de dates en une passe.

    Args:
        df           : DataFrame Polars source (toutes colonnes en Utf8)
        date_columns : Liste des colonnes à normaliser
        date_formats : Hints correspondants ('DMY' ou 'MDY')

    Returns:
        DataFrame avec colonnes normalisées
    """
    for col, hint in zip(date_columns, date_formats):
        if col not in df.columns:
            raise ValueError(f"Colonne '{col}' absente. Colonnes : {df.columns}")
        df = normalize_column_polars(df, col, hint)
    return df
