import time
import requests

# Payload pour le fichier de 28 Mo
payload_pandas = {
    "bucket": "raw",
    "file": "lst_of_users_anon_1.csv",
    "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"], # Adapte avec les vrais noms de colonnes si besoin
    "date_formats": ["MDY", "MDY", "MDY"],
    "engine": "pandas"
}

payload_polars = {**payload_pandas, "engine": "polars"}

# Pour Rust, l'engine n'est pas requis dans la structure mais le payload reste compatible
payload_rust = {
    "bucket": "raw",
    "file": "lst_of_users_anon_1.csv",
    "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"],
    "date_formats": ["MDY", "MDY", "MDY"]
}

def run_bench(url, payload, name):
    print(f"--- En cours : {name} ---")
    times = []
    # Règle du guide : Exécuter 3 fois pour noter la médiane
    for i in range(3):
        t0 = time.time()
        r = requests.post(url, json=payload)
        duration = time.time() - t0
        if r.status_code == 200:
            times.append(duration)
            print(f"  Lancer {i+1} : {duration:.2f}s")
        else:
            print(f"  Erreur Lancer {i+1} (Status {r.status_code}): {r.text}")
            return None
    
    times.sort()
    mediane = times[1] # La valeur du milieu sur 3 lancers
    print(f"-> Médiane {name} : {mediane:.2f}s\n")
    return mediane

if __name__ == "__main__":
    print("Début du Benchmark Global...\n")
    
    # 1. Test Pandas
    bench_pd = run_bench("http://localhost:8000/processDate", payload_pandas, "Pandas (FastAPI)")
    
    # 2. Test Polars
    bench_pl = run_bench("http://localhost:8000/processDate", payload_polars, "Polars (FastAPI)")
    
    # 3. Test Rust
    bench_rs = run_bench("http://localhost:8001/processDate", payload_rust, "Rust (Axum)")