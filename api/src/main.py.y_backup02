import io
import csv
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import boto3
from botocore.client import Config
import pandas as pd
import polars as pl

# Parseur de référence Python (8 priorités) — utilisé par le moteur Pandas uniquement
from api.src.parsers.date_parser import parse_date_cell

# *** NOUVEAU : Moteur Polars natif (expressions Rust, sans map_elements par cellule) ***
from api.src.parsers.polars_date_engine import normalize_date_columns

app = FastAPI(
    title="API ARTECI - Normalisation de Dates",
    description="API de traitement haute performance des formats de date mixtes.",
    version="3.0.0"
)

# ---------------------------------------------------------------------------
# CONFIGURATION MINIO
# ---------------------------------------------------------------------------
MINIO_URL = "http://localhost:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadminpassword"


def get_minio_client():
    """Initialise et retourne le client de stockage compatible S3."""
    return boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1"
    )


# ---------------------------------------------------------------------------
# STRUCTURES DE DONNÉES
# ---------------------------------------------------------------------------

class ProcessDateRequest(BaseModel):
    bucket: str = Field(..., description="Nom du compartiment d'entrée", example="raw")
    file: str = Field(..., description="Nom ou chemin du fichier CSV", example="lst_of_users_anon_1.csv")
    date_columns: List[str] = Field(..., description="Liste des colonnes à normaliser", example=["created_at", "updated_date"])
    date_formats: List[str] = Field(..., description="Priorité de format par colonne ('DMY' ou 'MDY')", example=["DMY", "MDY"])
    engine: Optional[str] = Field("polars", description="Moteur de traitement: 'pandas' ou 'polars'")


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/columns")
def get_columns(
    bucket: str = Query(..., description="Nom du compartiment MinIO"),
    file: str = Query(..., description="Nom du fichier (ex: .csv, .xlsx, .log)")
):
    """Détecte le type de fichier et extrait les colonnes de manière adaptée."""
    s3_client = get_minio_client()
    file_extension = file.split('.')[-1].lower() if '.' in file else ''

    try:
        if file_extension == 'csv':
            response = s3_client.get_object(Bucket=bucket, Key=file, Range='bytes=0-4096')
            text_chunk = response['Body'].read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(text_chunk), delimiter=';')
            return {"columns": next(csv_reader)}

        elif file_extension in ['xlsx', 'xls']:
            return {"columns": ["Besoin d'intégrer openpyxl / polars.read_excel"]}

        elif file_extension in ['log', 'txt']:
            return {"columns": ["line_content"]}

        else:
            raise HTTPException(status_code=400, detail=f"Extension .{file_extension} non supportée.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/processDate")
def process_date(payload: ProcessDateRequest):
    s3_client = get_minio_client()

    # 1. Récupération du fichier source
    try:
        s3_object = s3_client.get_object(Bucket=payload.bucket, Key=payload.file)
        file_bytes = s3_object['Body'].read()
    except s3_client.exceptions.NoSuchBucket:
        raise HTTPException(status_code=404, detail=f"Bucket source '{payload.bucket}' introuvable.")
    except s3_client.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail=f"Fichier '{payload.file}' introuvable dans le bucket.")

    # 2. Validation de la cohérence des listes
    if len(payload.date_columns) != len(payload.date_formats):
        raise HTTPException(
            status_code=400,
            detail="Les listes 'date_columns' et 'date_formats' doivent avoir la même taille."
        )

    # =========================================================================
    # MOTEUR PANDAS — Délégation complète au parseur de référence (8 priorités)
    # =========================================================================
    if payload.engine == "pandas":
        try:
            df_pd = pd.read_csv(io.BytesIO(file_bytes), sep=';', dtype=str)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Pandas) : {str(e)}")

        for col in payload.date_columns:
            if col not in df_pd.columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Colonne '{col}' absente. Colonnes disponibles : {list(df_pd.columns)}"
                )

        # Chaque cellule passe par le parseur de référence avec ses 8 priorités
        for col, hint in zip(payload.date_columns, payload.date_formats):
            df_pd[col] = df_pd[col].apply(lambda v: parse_date_cell(v, hint))

        preview_data = df_pd.head(100).to_dict(orient="records")
        output_buffer = io.BytesIO()
        df_pd.to_csv(output_buffer, index=False, sep=';')
        output_buffer.seek(0)

    # =========================================================================
    # MOTEUR POLARS — Expressions natives Rust (SANS map_elements par cellule)
    #
    # ARCHITECTURE :
    #   P1  (Unix timestamp)      → cast Int64 + dt.strftime vectorisé
    #   P2  (ISO 8601 + TZ)       → str.to_datetime natif + convert_time_zone
    #   P3  (ISO datetime)        → str.to_datetime natif
    #   P4  (ISO date)            → str.to_datetime natif
    #   P5  (ISO compact yyyyMMdd)→ str.to_datetime natif
    #   P6a (mois EN abrégés)     → str.replace vectorisé + str.to_datetime
    #   P6b (mois FR textuels)    → map_batches sur batch entier (1 appel Python/colonne)
    #   P7  (ambiguïté DMY/MDY)   → when/then arithmétique Polars + str.zfill
    #   P8  (fallback)            → valeur originale inchangée
    #
    # Gain attendu vs. map_elements : x5 à x20 selon le profil des données
    # =========================================================================
    else:
        try:
            # infer_schema_length=0 : tout en Utf8, zéro inférence de type
            df_pl = pl.read_csv(io.BytesIO(file_bytes), infer_schema_length=0, separator=';')
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Polars) : {str(e)}")

        for col in payload.date_columns:
            if col not in df_pl.columns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Colonne '{col}' absente. Colonnes disponibles : {df_pl.columns}"
                )

        # Une seule passe sur le DataFrame pour toutes les colonnes
        df_pl = normalize_date_columns(
            df_pl,
            payload.date_columns,
            payload.date_formats,
        )

        preview_data = df_pl.head(100).to_dicts()
        output_buffer = io.BytesIO()
        df_pl.write_csv(output_buffer, separator=';')
        output_buffer.seek(0)

    # 3. Sauvegarde dans le bucket 'processeddata'
    try:
        s3_client.put_object(
            Bucket="processeddata",
            Key=payload.file,
            Body=output_buffer.getvalue()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur d'écriture MinIO : {str(e)}")

    return {
        "status": "success",
        "engine_used": payload.engine,
        "preview": preview_data
    }
