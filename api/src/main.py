import io
import csv
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import boto3
from botocore.client import Config
import pandas as pd
import polars as pl

# Import de notre parseur robuste créé à l'étape précédente
from api.src.parsers.date_parser import parse_date_cell

app = FastAPI(
    title="API ARTECI - Normalisation de Dates",
    description="Squelette d'API pour le traitement haute performance des formats de date mixtes.",
    version="1.0.0"
)

# Configuration du client MinIO S3
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
# STRUCTURES DE DONNÉES (Validation Pydantic)
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

# Modifiez l'endpoint GET /columns pour intégrer la détection de format
@app.get("/columns")
def get_columns(
    bucket: str = Query(..., description="Nom du compartiment MinIO"),
    file: str = Query(..., description="Nom du fichier (ex: .csv, .xlsx, .log)")
):
    """
    Détecte le type de fichier et extrait les colonnes de manière adaptée.
    """
    s3_client = get_minio_client()
    file_extension = file.split('.')[-1].lower() if '.' in file else ''

    try:
        # --- CAS 1 : FICHIER CSV ---
        if file_extension == 'csv':
            response = s3_client.get_object(Bucket=bucket, Key=file, Range='bytes=0-4096')
            text_chunk = response['Body'].read().decode('utf-8')
            # Remplacer la ligne csv.reader par celle-ci pour gérer le point-virgule :
            csv_reader = csv.reader(io.StringIO(text_chunk), delimiter=';')
            return {"columns": next(csv_reader)}

        # --- CAS 2 : FICHIER EXCEL (.xlsx) ---
        elif file_extension in ['xlsx', 'xls']:
            # Note : Excel n'est pas un fichier texte, on ne peut pas lire "les 4096 premiers octets".
            # TODO : Utiliser openpyxl / Calamine (en Polars) pour charger uniquement les en-têtes de la feuille 1
            return {"columns": ["Besoin d'intégrer openpyxl / polars.read_excel"]}

        # --- CAS 3 : FICHIER LOGS (.log ou .txt) ---
        elif file_extension in ['log', 'txt']:
            # Souvent les logs n'ont pas d'en-tête (ex: Apache/Nginx logs). 
            # On retourne des colonnes nommées par défaut ou basées sur un pattern Regex (ex: ["ip", "timestamp", "request", "status"])
            return {"columns": ["line_content"]}

        else:
            raise HTTPException(status_code=400, detail=f"Extension .{file_extension} non supportée d'office.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/processDate")
def process_date(payload: ProcessDateRequest):
    s3_client = get_minio_client()
    
    # 1. Validation de l'existence du fichier d'entrée
    try:
        s3_object = s3_client.get_object(Bucket=payload.bucket, Key=payload.file)
        file_bytes = s3_object['Body'].read()
    except s3_client.exceptions.NoSuchBucket:
        raise HTTPException(status_code=404, detail=f"Bucket source '{payload.bucket}' introuvable.")
    except s3_client.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail=f"Fichier '{payload.file}' introuvable dans le bucket.")

    # 2. Validation des formats
    if len(payload.date_columns) != len(payload.date_formats):
        raise HTTPException(status_code=400, detail="Les listes de colonnes et de formats doivent être de même taille.")

    # =========================================================================
    # ÉTAPE 2-A : MOTEUR PANDAS (Baseline)
    # =========================================================================
    if payload.engine == "pandas":
        try:
            df_pd = pd.read_csv(io.BytesIO(file_bytes), sep=';')
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Pandas) : {str(e)}")

        # Validation de l'existence des colonnes
        for col in payload.date_columns:
            if col not in df_pd.columns:
                raise HTTPException(status_code=400, detail=f"Colonne '{col}' absente. Colonnes disponibles : {list(df_pd.columns)}")

        # Application du parsing
        for col, hint in zip(payload.date_columns, payload.date_formats):
            df_pd[col] = df_pd[col].apply(lambda v: parse_date_cell(v, hint))

        # Aperçu et export mémoire
        preview_data = df_pd.head(100).to_dict(orient="records")
        output_buffer = io.BytesIO()
        df_pd.to_csv(output_buffer, index=False)
        output_buffer.seek(0)

    # =========================================================================
    # ÉTAPE 2-B : MOTEUR POLARS (Version optimisée)
    # =========================================================================
    # =========================================================================
    # ÉTAPE 2-B : MOTEUR POLARS (Version Réellement Optimisée)
    # =========================================================================
    else:
        try:
            # On charge tout en texte (String)
            df_pl = pl.read_csv(io.BytesIO(file_bytes), infer_schema_length=0, separator=';')
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Polars) : {str(e)}")

        for col in payload.date_columns:
            if col not in df_pl.columns:
                raise HTTPException(status_code=400, detail=f"Colonne '{col}' absente.")

        # Application du parsing purement vectoriel (Exécuté à 100% en C++/Rust)
        for col, hint in zip(payload.date_columns, payload.date_formats):
            
            # 1. Définition des formats prioritaires selon le hint
            if hint == 'DMY':
                formats = ["%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"]
            else:
                formats = ["%m/%d/%Y %H:%M:%S", "%m-%d-%Y %H:%M:%S", "%m/%d/%Y", "%m-%d-%Y"]
                
            # Formats secondaires de secours (ISO, etc.)
            formats.extend(["%Y-%m-%dT%H:%M:%S%.fZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"])

            # 2. Construction de la cascade de tentatives native
            # Tentative avec le premier format
            parsed_expr = pl.col(col).str.to_datetime(formats[0], strict=False)
            
            # Remplacement des échecs (Nulls) par les formats suivants
            for fmt in formats[1:]:
                parsed_expr = parsed_expr.fill_null(pl.col(col).str.to_datetime(fmt, strict=False))
                
            # 3. Formatage final vers la chaîne cible : 'JJ-MM-AAAA HH:mm:ss'
            # Règle métier : si le parsing a échoué partout (Null), on réinjecte la valeur d'origine pl.col(col)
            final_expr = parsed_expr.dt.strftime("%d-%m-%Y %H:%M:%S").fill_null(pl.col(col))
            
            # Application instantanée sur la colonne
            df_pl = df_pl.with_columns(final_expr.alias(col))

        preview_data = df_pl.head(100).to_dicts()
        output_buffer = io.BytesIO()
        df_pl.write_csv(output_buffer)
        output_buffer.seek(0)

    # 3. Sauvegarde dans le bucket 'processeddata' avec remplacement sans doublon
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
