# Résultats du Benchmark Technique (Fichier de 28 Mo)

*   **Machine de test :** Kali Linux (Environnement Virtuel)
*   **Moteur PANDAS (Baseline Naïve via `.apply()`) :** 96,11 secondes
*   **Moteur POLARS (Optimisation Vectorielle Native) :** 2,39 secondes
*   **Facteur d'accélération :** **40,2x plus rapide**

### Analyse technique
L'implémentation naïve avec Pandas force l'utilisation du Global Interpreter Lock (GIL) de Python pour traiter les lignes une par une. L'optimisation Polars utilise des expressions natives compilées, éliminant le surcoût de l'interpréteur Python et parallélisant efficacement le parsing de la cascade sur l'ensemble des cœurs CPU disponibles.
