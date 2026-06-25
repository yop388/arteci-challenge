import time
import requests

url = "http://localhost:8000/processDate"

# Configurez ici le nom réel de notre fichier de 28 Mo présent dans votre bucket 'raw'
payload_base = {
    "bucket": "raw",
    "file": "lst_of_users_anon_1.csv", 
    "date_columns": ["DATE_CREATION", "DATE_DESACTIVATION", "DATE_DERNIERE_CONNECTION_1"], # Remplacer par le vrai nom de la colonne du fichier
    "date_formats": ["MDY", "MDY", "MDY"]  # Remplacer par le(s) vrai(s) format de colonnes du fichier
}

def run_bench(engine_name):
    payload = payload_base.copy()
    payload["engine"] = engine_name
    
    t0 = time.time()
    response = requests.post(url, json=payload)
    elapsed = time.time() - t0
    
    if response.status_code == 200:
        print(f"Moteur [{engine_name.upper()}] -> Temps d'exécution : {elapsed:.2f} secondes | Statut : {response.status_code}")
    else:
        print(f"Échec pour {engine_name} -> Statut {response.status_code} : {response.text}")
    return elapsed

if __name__ == "__main__":
    print("🚀 Lancement du benchmark comparatif...")
    time_pandas = run_bench("pandas")
    time_polars = run_bench("polars")
    
    gain = (time_pandas / time_polars) if time_polars > 0 else 0
    print(f"\n Polars est environ {gain:.1f}x plus rapide que Pandas sur ce fichier !")
