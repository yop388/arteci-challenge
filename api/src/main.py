import io
import csv
import os  # <-- NOUVEAU : Pour lire les variables d'environnement Docker
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import boto3
from botocore.client import Config
import pandas as pd
import polars as pl

# --- AJOUTS OPENTELEMETRY ---
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
# ----------------------------

# Parseur de référence Python (8 priorités)
from api.src.parsers.date_parser import parse_date_cell
# Moteur Polars natif
from api.src.parsers.polars_date_engine import normalize_date_columns

# ---------------------------------------------------------------------------
# INITIALISATION CONFIGURATION & OPENTELEMETRY
# ---------------------------------------------------------------------------
# Configurer le fournisseur de traces global
provider = TracerProvider()
processor = BatchSpanProcessor(OTLPSpanExporter()) # Utilise les variables d'env OTEL_EXPORTER_OTLP_ENDPOINT
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

# Récupérer le traceur pour créer des spans personnalisés si besoin
tracer = trace.get_tracer(__name__)

app = FastAPI(
    title="API ARTECI - Normalisation de Dates",
    description="API de traitement haute performance des formats de date mixtes.",
    version="3.0.0"
)

# Instrumenter FastAPI automatiquement
FastAPIInstrumentor.instrument_app(app)

# Instrumenter Boto3 (MinIO) automatiquement pour suivre les performances S3
BotocoreInstrumentor().instrument()

# ---------------------------------------------------------------------------
# CONFIGURATION MINIO (Modifiée pour supporter Docker et le local)
# ---------------------------------------------------------------------------
MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9000")
ACCESS_KEY = os.getenv("ACCESS_KEY", "minioadmin")
SECRET_KEY = os.getenv("SECRET_KEY", "minioadminpassword")


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

# ... [Le reste de tes structures de données reste inchangé] ...

class ProcessDateRequest(BaseModel):
    bucket: str = Field(..., description="Nom du compartiment d'entrée", example="raw")
    file: str = Field(..., description="Nom ou chemin du fichier CSV", example="lst_of_users_anon_1.csv")
    date_columns: List[str] = Field(..., description="Liste des colonnes à normaliser", example=["created_at", "updated_date"])
    date_formats: List[str] = Field(..., description="Priorité de format par colonne ('DMY' ou 'MDY')", example=["DMY", "MDY"])
    engine: Optional[str] = Field("polars", description="Moteur de traitement: 'pandas' ou 'polars'")


@app.get("/columns")
def get_columns(
    bucket: str = Query(..., description="Nom du compartiment MinIO"),
    file: str = Query(..., description="Nom du fichier (ex: .csv, .xlsx, .log)")
):
    # La capture de cette fonction est automatique grâce à FastAPIInstrumentor
    s3_client = get_minio_client()
    file_extension = file.split('.')[-1].lower() if '.' in file else ''

    try:
        if file_extension == 'csv':
            response = s3_client.get_object(Bucket=bucket, Key=file, Range='bytes=0-4096')
            text_chunk = response['Body'].read().decode('utf-8')
            csv_reader = csv.reader(io.StringIO(text_chunk), delimiter=';')
            return {"columns": next(csv_reader)}
        # ... [Reste du code de get_columns] ...
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/processDate")
def process_date(payload: ProcessDateRequest):
    s3_client = get_minio_client()

    try:
        s3_object = s3_client.get_object(Bucket=payload.bucket, Key=payload.file)
        file_bytes = s3_object['Body'].read()
    except s3_client.exceptions.NoSuchBucket:
        raise HTTPException(status_code=404, detail=f"Bucket source '{payload.bucket}' introuvable.")
    except s3_client.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail=f"Fichier '{payload.file}' introuvable dans le bucket.")

    if len(payload.date_columns) != len(payload.date_formats):
        raise HTTPException(status_code=400, detail="Les listes 'date_columns' et 'date_formats' doivent avoir la même taille.")

    # AJOUT : Span personnalisé pour mesurer précisément la phase lourde de calcul (Pandas vs Polars)
    with tracer.start_as_current_span("date_normalization_engine") as span:
        span.set_attribute("data.engine", payload.engine)
        span.set_attribute("data.columns_count", len(payload.date_columns))

        if payload.engine == "pandas":
            try:
                df_pd = pd.read_csv(io.BytesIO(file_bytes), sep=';', dtype=str)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Pandas) : {str(e)}")

            for col in payload.date_columns:
                if col not in df_pd.columns:
                    raise HTTPException(status_code=400, detail=f"Colonne '{col}' absente.")

            for col, hint in zip(payload.date_columns, payload.date_formats):
                df_pd[col] = df_pd[col].apply(lambda v: parse_date_cell(v, hint))

            preview_data = df_pd.head(100).to_dict(orient="records")
            output_buffer = io.BytesIO()
            df_pd.to_csv(output_buffer, index=False, sep=';')
            output_buffer.seek(0)

        else:
            try:
                df_pl = pl.read_csv(io.BytesIO(file_bytes), infer_schema_length=0, separator=';')
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Erreur de lecture CSV (Polars) : {str(e)}")

            for col in payload.date_columns:
                if col not in df_pl.columns:
                    raise HTTPException(status_code=400, detail=f"Colonne '{col}' absente.")

            df_pl = normalize_date_columns(df_pl, payload.date_columns, payload.date_formats)

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