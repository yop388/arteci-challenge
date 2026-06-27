import time
import requests

# Payload pour le(s) fichier(s) 
payload_pandas = {
    "bucket": "raw",
    "file": "lst_of_users_anon_1.csv",
    "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"], 
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
    
    t0 = time.time()
    r = requests.post(url, json=payload)
    duration = time.time() - t0
    
    if r.status_code == 200:
        print(f"-> Temps d'exécution {name} : {duration:.2f}s\n")
        return duration
    else:
        print(f"  Erreur (Status {r.status_code}): {r.text}\n")
        return None

if __name__ == "__main__":
    print("Début du Benchmark Global (1 seul lancer par moteur)...\n")
    
    # 1. Test Pandas
    bench_pd = run_bench("http://localhost:8000/processDate", payload_pandas, "Pandas (FastAPI)")
    
    # 2. Test Polars
    bench_pl = run_bench("http://localhost:8000/processDate", payload_polars, "Polars (FastAPI)")
    
    # 3. Test Rust
    bench_rs = run_bench("http://localhost:8001/processDate", payload_rust, "Rust (Axum)")