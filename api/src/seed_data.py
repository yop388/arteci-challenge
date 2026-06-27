import os
import boto3
from botocore.client import Config

# --- MODIFICATION ICI : Lit l'environnement Docker/Kubernetes, ou utilise localhost par défaut ---
MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9000")
ACCESS_KEY = os.getenv("ACCESS_KEY", "minioadmin")
SECRET_KEY = os.getenv("SECRET_KEY", "minioadminpassword")
BUCKET_NAME = "raw"
PROCESSED_BUCKET = "processeddata"


def ensure_buckets_exist(s3_client):
    """Crée automatiquement les buckets s'ils n'existent pas."""
    for bucket in [BUCKET_NAME, PROCESSED_BUCKET]:
        try:
            s3_client.head_bucket(Bucket=bucket)
            print(f"Le bucket '{bucket}' existe déjà.")
        except s3_client.exceptions.ClientError:
            print(f"Création du bucket '{bucket}'...")
            s3_client.create_bucket(Bucket=bucket)


# Chemin du fichier CSV
CSV_FILE_PATH = "docs/lst_of_users_anon_3.csv"

def upload_to_minio():
    # 1. Initialisation du client compatible S3
    s3_client = boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1" # Valeur requise par défaut, ignorée par MinIO
    )
    
    # Les buckets sont créés en premier, quoi qu'il arrive avec le fichier
    ensure_buckets_exist(s3_client)

    # 2. Vérification de l'existence et de l'état du fichier local
    if not os.path.exists(CSV_FILE_PATH):
        print(f"⚠️ Attention : Le fichier '{CSV_FILE_PATH}' est absent. Les buckets ont été créés, mais aucun fichier n'a été téléversé.")
        return # On arrête proprement la fonction ici sans bloquer le script

    if os.path.getsize(CSV_FILE_PATH) == 0:
        print(f"⚠️ Attention : Le fichier '{CSV_FILE_PATH}' existe mais il est VIDE. Les buckets ont été créés, mais aucun fichier n'a été téléversé.")
        return # On arrête proprement la fonction ici aussi

    # 3. Envoi du fichier (uniquement si le fichier existe et n'est pas vide)
    destination_name = os.path.basename(CSV_FILE_PATH)
    print(f"Téléversement de {CSV_FILE_PATH} vers le bucket '{BUCKET_NAME}'...")

    try:
        s3_client.upload_file(CSV_FILE_PATH, BUCKET_NAME, destination_name)
        print(f"⚡ Succès ! Fichier disponible sous : {BUCKET_NAME}/{destination_name}")
    except Exception as e:
        print(f"Erreur lors du téléversement : {e}")

if __name__ == "__main__":
    upload_to_minio()