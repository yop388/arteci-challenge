# Résultats du Benchmark Technique

*   **Machine de test :** Kali Linux (Environnement Virtuel)
*   **Moteur PANDAS (Baseline Naïve via `.apply()`) :** 96,11 secondes
*   **Moteur POLARS (Optimisation Vectorielle Native) :** 2,39 secondes
*   **Facteur d'accélération :** **40,2x plus rapide**

### Analyse technique
L'implémentation naïve avec Pandas force l'utilisation du Global Interpreter Lock (GIL) de Python pour traiter les lignes une par une. L'optimisation Polars utilise des expressions natives compilées, éliminant le surcoût de l'interpréteur Python et parallélisant efficacement le parsing de la cascade sur l'ensemble des cœurs CPU disponibles.

# Résultats du Benchmark Global (Version Corrigée)

* **Environnement de test :** Machine locale (12 Go RAM DDR3 1060 MT/s, CPU multi-cœurs).
* **Méthodologie :** Mesure complète de l'appel HTTP (Téléchargement MinIO -> Parsing -> Écriture MinIO). Médiane sur 3 lancers.

| Fichier | Taille | Lignes | Pandas (s) | Polars (s) | Rust (s) | Comportement Matériel Observé |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `lst_of_users_anon_1.csv` | ~28 Mo | 320 399 | 111.25s | 8.80s | 3.47s | Rust plus rapide sur petit volume (pas d'overhead). |
| `lst_of_users_anon_2.csv` | ~182 Mo | 2 119 517 | *N/A* | 73.45s | 41.33s | Transition de charge. |
| `lst_of_users_anon_3.csv` | ~931 Mo | 10 799 773 | *N/A* | **478.43s** | **129.95s** | Polars sature le CPU à 100% (mais perdant). Rust sature la RAM à 85%. |