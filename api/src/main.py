import io
import csv
from typing import List
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import boto3
from botocore.client import Config

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
    file: str = Field(..., description="Nom ou chemin du fichier CSV", example="sample_dates.csv")
    date_columns: List[str] = Field(..., description="Liste des colonnes à normaliser", example=["created_at", "updated_date"])
    date_formats: List[str] = Field(..., description="Priorité de format par colonne ('DMY' ou 'MDY')", example=["DMY", "MDY"])

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
            csv_reader = csv.reader(io.StringIO(text_chunk))
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
    """
    Reçoit les paramètres de normalisation, valide la cohérence des listes,
    et renvoie une réponse factice (MOCK / TODO) contenant un aperçu statique.
    """
    # Validation logique personnalisée : vérifier que le nombre de formats correspond au nombre de colonnes
    if len(payload.date_columns) != len(payload.date_formats):
        raise HTTPException(
            status_code=420, 
            detail="La liste 'date_columns' et la liste 'date_formats' doivent avoir exactement la même taille."
        )
    
    # Validation des choix de formats supportés
    for fmt in payload.date_formats:
        if fmt not in ["DMY", "MDY"]:
            raise HTTPException(
                status_code=422,
                detail=f"Format '{fmt}' non supporté. Les valeurs autorisées sont uniquement 'DMY' ou 'MDY'."
            )

    # Réponse factice (MOCK) simulant l'aperçu des 100 premières lignes requis par le cahier des charges
    # TODO: Remplacer ce bloc par l'exécution de votre logique Polars/Rust lors de la prochaine étape
    mock_preview = [
        {"id": "1", "username": "alice", "created_at": "25-12-2024 14:30:00", "updated_date": "25-12-2024 00:00:00"},
        {"id": "2", "username": "bob", "created_at": "15-06-2024 08:12:00", "updated_date": "15-06-2024 08:12:00"},
        {"id": "3", "username": "charlie", "created_at": "malformed_date", "updated_date": "31-12-2024 23:59:59"}
    ]

    return {
        "status": "success",
        "message": "Traitement simulé avec succès (Logique de parsing en cours d'intégration).",
        "preview": mock_preview
    }
