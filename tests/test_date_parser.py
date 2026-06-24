import pytest
from api.src.parsers.date_parser import parse_date_cell

def test_priorite_1_timestamp():
    assert parse_date_cell("1711108200") == "22-03-2024 11:50:00"

def test_priorite_2_iso_complet():
    assert parse_date_cell("2024-03-22T14:30:00Z") == "22-03-2024 14:30:00"

def test_priorite_3_iso_espace():
    assert parse_date_cell("2024-03-22 14:30:00") == "22-03-2024 14:30:00"

def test_priorite_4_iso_court():
    assert parse_date_cell("2024-03-22") == "22-03-2024 00:00:00"

def test_priorite_5_iso_compact():
    assert parse_date_cell("20240322") == "22-03-2024 00:00:00"

def test_priorite_6_mois_textuel():
    assert parse_date_cell("22 mars 2024") == "22-03-2024 00:00:00"
    assert parse_date_cell("Mar 22 2024") == "22-03-2024 00:00:00"

def test_priorite_7_ambigu_strict_avec_hint():
    # Cas ambigu : 03/04/2024 (Peut être 3 Avril ou 4 Mars)
    assert parse_date_cell("03/04/2024", hint="DMY") == "03-04-2024 00:00:00"
    assert parse_date_cell("03/04/2024", hint="MDY") == "04-03-2024 00:00:00"

def test_priorite_7_non_ambigu_malgre_hint():
    # Jour > 12 (22/03/2024), le hint MDY doit être ignoré au profit de la logique métier
    assert parse_date_cell("22/03/2024", hint="MDY") == "22-03-2024 00:00:00"

def test_priorite_8_formats_avec_heure():
    assert parse_date_cell("22/03/2024 14:30:00", hint="DMY") == "22-03-2024 14:30:00"

def test_regle_cellule_invalide():
    assert parse_date_cell("texte_corrompu_123") == "texte_corrompu_123"

def test_regle_cellule_vide():
    assert parse_date_cell("") == ""
    assert parse_date_cell(None) == ""
    assert parse_date_cell("NaN") == ""
