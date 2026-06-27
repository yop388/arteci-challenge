import sys
import time
import requests

if __name__ == "__main__":
    # Vérifie si l'utilisateur a bien donné un nom de fichier en argument
    if len(sys.argv) < 2:
        print("Erreur : Veuillez donner le nom du fichier CSV en paramètre.")
        print("Exemple : python script.py mon_fichier.csv")
        sys.exit(1)

    # Récupère le nom du fichier depuis la ligne de commande
    csv_filename = sys.argv[1]

    print(f"Début du Benchmark Global pour le fichier : {csv_filename}\n")

    # Payload de base pour Pandas avec le fichier dynamique
    payload_pandas = {
        "bucket": "raw",
        "file": csv_filename,
        "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"], 
        "date_formats": ["MDY", "MDY", "MDY"],
        "engine": "pandas"
    }

    payload_polars = {**payload_pandas, "engine": "polars"}

    # Payload pour Rust avec le fichier dynamique
    payload_rust = {
        "bucket": "raw",
        "file": csv_filename,
        "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"],
        "date_formats": ["MDY", "MDY", "MDY"]
    }

    def run_bench(url, payload, name, filename):
        # Ajout du nom du fichier dans le message "En cours"
        print(f"--- En cours : {name} [Fichier: {filename}] ---")
        
        t0 = time.time()
        r = requests.post(url, json=payload)
        duration = time.time() - t0
        
        if r.status_code == 200:
            print(f"-> Temps d'exécution {name} : {duration:.2f}s\n")
            return duration
        else:
            print(f"  Erreur (Status {r.status_code}): {r.text}\n")
            return None

    # 1. Test Pandas
    bench_pd = run_bench("http://localhost:8000/processDate", payload_pandas, "Pandas (FastAPI)", csv_filename)
    
    # 2. Test Polars
    bench_pl = run_bench("http://localhost:8000/processDate", payload_polars, "Polars (FastAPI)", csv_filename)
    
    # 3. Test Rust
    bench_rs = run_bench("http://localhost:8001/processDate", payload_rust, "Rust (Axum)", csv_filename)
