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
        except s3_client.exceptions.ClientError:
            print(f"Création du bucket '{bucket}'...")
            s3_client.create_bucket(Bucket=bucket)


# remplacer ceci par le chemin absolu de l'un de notre CSV fournis
# Pour le test, nous créons un faux fichier CSV à la volée s'il n'existe pas
CSV_FILE_PATH = "/home/othniel/Téléchargements/Challenge ARTEFACT Recrutement/lst_of_users_anon_3.csv"

def create_sample_csv():
    """Génère un fichier CSV local contenant des formats de date mixtes."""
    content = (
        "id,username,created_at,updated_date\n"
        "1,alice,25/12/2024 14:30:00,12/25/2024\n"       # Ligne 1: DMY / MDY
        "2,bob,2024-06-15T08:12:00Z,1718439120\n"        # Ligne 2: ISO / Timestamp
        "3,charlie,malformed_date,2024-12-31 23:59:59\n" # Ligne 3: Invalide / Tech
    )
    with open(CSV_FILE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✓ Fichier de test local généré : {CSV_FILE_PATH}")

def upload_to_minio():
    # Initialisation du client compatible S3
    s3_client = boto3.client(
        "s3",
        endpoint_url=MINIO_URL,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1" # Valeur requise par défaut, ignorée par MinIO
    )
    ensure_buckets_exist(s3_client)

    # 2. Vérification de l'existence du fichier local
    if not os.path.exists(CSV_FILE_PATH):
        print("Fichier CSV non trouvé. Création d'un fichier de test...")
        create_sample_csv()

    # 3. Envoi du fichier
    destination_name = os.path.basename(CSV_FILE_PATH)
    print(f"Téléversement de {CSV_FILE_PATH} vers le bucket '{BUCKET_NAME}'...")

    try:
        s3_client.upload_file(CSV_FILE_PATH, BUCKET_NAME, destination_name)
        print(f"⚡ Succès ! Fichier disponible sous : {BUCKET_NAME}/{destination_name}")
    except Exception as e:
        print(f"Erreur lors du téléversement : {e}")

if __name__ == "__main__":
    upload_to_minio()
