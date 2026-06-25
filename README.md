# ARTECI — API Haute Performance de Normalisation de Dates

Ce projet s'inscrit dans le cadre du Challenge DevOps / Data Platform d'Artefact. Il implémente une API robuste capable de traiter à haute performance et à grande échelle des fichiers de données mixtes comportant des ambiguïtés de formats de date (ex: DMY vs MDY).

---

## Image Docker de Production

L'image de l'API normalisée (optimisée via un processus de build multi-stage) est disponible publiquement sur DockerHub :
* **Lien de la Registry :** [https://hub.docker.com/r/othnielparfait/arteci-api](https://hub.docker.com/r/othnielparfait/arteci-api)
* **Commande de téléchargement :** 
```bash
  docker pull othnielparfait/arteci-api:latest
```

  # Documentation du Projet

## 🏗️ Choix Techniques & Décision d'Architecture

Après une phase intensive de R&D et d'optimisation (Blocs B1 à B3), les moteurs de calcul **Pandas**, **Polars** et **Rust** ont été mis en concurrence sur des volumétries croissantes.

### Synthèse des Résultats (Médiane sur 3 lancers)
* **Petit volume (28 Mo – 320 399 lignes) :** Rust (~3.75s) < Polars (~10.19s) < Pandas (~115.12s)
* **Gros volume (931 Mo – 10 799 773 lignes) :** Polars (~47.6s) < Rust (~134.40s) < Pandas (Non exécuté - Timeout)

### Décision Finale : Choix de Polars (Python)
Bien que Rust se soit montré plus agile sur les petits fichiers, Polars a largement surclassé l'implémentation Rust sur le fichier critique de 931 Mo par un facteur de 2.8x, tout en sollicitant le matériel de façon optimale :

1. **Saturation CPU à 100% :** Polars distribue nativement ses expressions vectorielles de manière hautement parallèle sur l'ensemble des cœurs.
2. **Efficience Mémoire sous Contrainte :** Sur une architecture matérielle restreinte (12 Go RAM DDR3 1060 MT/s), la boucle séquentielle de notre parseur Rust a provoqué un engorgement mémoire (*Memory Churn* à 85% de RAM) à cause des allocations de chaînes de caractères. Polars, via ses structures Apache Arrow, a préservé la mémoire et validé son intégration comme moteur de production idéal.

---

## 🚀 Instructions de Démarrage en Local

### 1. Variables d'environnement (`.env`)
Créez un fichier `.env` à la racine en vous basant sur le modèle `.env.example` :

```text
MINIO_URL=http://localhost:9000
ACCESS_KEY=minioadmin
SECRET_KEY=minioadminpassword
```

### 2. Lancement avec Docker Compose
Pour démarrer instantanément toute la stack (L'API conteneurisée + le stockage MinIO configuré) :

```bash
docker compose up -d --build
```

### 3. Utilisation de l'API

#### 🎯 Vérifier les colonnes d'un fichier d'entrée (Extraction d'en-tête optimisée) :
```bash
curl -X GET "http://localhost:8000/columns?bucket=raw&file=lst_of_users_anon_1.csv"
```

#### 🎯 Lancer le traitement de normalisation :
```bash
curl -X POST "http://localhost:8000/processDate" \
     -H "Content-Type: application/json" \
     -d '{
       "bucket": "raw",
       "file": "lst_of_users_anon_1.csv",
       "date_columns": ["created_at", "updated_date"],
       "date_formats": ["DMY", "MDY"],
       "engine": "polars"
     }'
```
Le fichier nettoyé sera automatiquement sauvegardé dans le bucket `processeddata`.

---

## 🐳 Déploiement Kubernetes (K8s)

Les manifests d'infrastructure se situent dans le dossier `k8s/`. Vous pouvez valider la syntaxe et simuler le déploiement sur votre cluster local (Minikube / Kind) à l'aide de la commande suivante :

```bash
kubectl apply --dry-run=client -f k8s/
```

---

## ⚠️ Limites Actuelles et Améliorations Futures

* **Formats non couverts :** Les dates textuelles complexes contenant des noms de mois en toutes lettres (ex: "25 Juin 2026") ou des fuseaux horaires exotiques non ISO ne sont pas encore pris en charge par la cascade d'expressions vectorielles.
* **Simplifications assumées :** L'API considère actuellement que le séparateur des fichiers CSV est systématiquement le point-virgule ( `;` ). Une détection automatique du délimiteur (*sniffer*) constituerait une évolution majeure pour la robustesse de la Data Platform.
