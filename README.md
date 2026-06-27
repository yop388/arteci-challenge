# ARTECI - API Haute Performance de Normalisation de Dates

Ce projet s'inscrit dans le cadre du Challenge DevOps / Data Platform d'Artefact. Il implémente une API robuste, scalable et cloud-native capable de traiter à haute performance des fichiers de données mixtes comportant des ambiguïtés de formats de date (ex: DMY vs MDY).

---

## 🐳 Image Docker de Production & Automatisation CI/CD

L'image finale de l'API (basée sur **FastAPI** et propulsée par le framework de données **Polars**) est disponible sur DockerHub :
* **Lien de la Registry :** [https://hub.docker.com/r/othnielparfait/arteci-api](https://hub.docker.com/r/othnielparfait/arteci-api)
* **Commande de téléchargement :**
```bash
docker pull othnielparfait/arteci-api:latest
```

### ⚙️ Pipeline CI/CD (GitHub Actions)
Le projet intègre un pipeline d'Intégration Continue (`.github/workflows/deploy.yml`). À chaque `git push` sur la branche `main`, le code est récupéré, testé, et compilé automatiquement sous forme d'image Docker, puis poussé sur Docker Hub. 
* **Sécurité :** L'authentification se fait de manière sécurisée via des secrets GitHub (`DOCKERHUB_USERNAME` et `DOCKERHUB_TOKEN`) liés à un jeton d'accès Docker Hub (Read & Write).

---

## 🏗️ Choix Techniques & Arbitrages d'Architecture

Dans le cadre de notre démarche d'ingénierie, les performances des moteurs Pandas, Polars (Python) et Rust ont été rigoureusement mesurées sur des volumétries industrielles.

### Synthèse des Résultats (Médiane sur 3 lancers)

**Environnement de test :** Machine locale (12 Go RAM DDR3 1060 MT/s, CPU multi-cœurs).
**Méthodologie :** Mesure complète de l'appel HTTP (Téléchargement MinIO -> Parsing -> Écriture MinIO).

* **volume (28 Mo : 320 399 lignes) :** Rust (~3.47s) < Polars (~8.80s) < Pandas (~111.25s)
* **volume (182 Mo : 2 119 517 ) :** Rust (~41.33s) < Polars (~73.45s) < Pandas (*N/A*)
* **volume (931 Mo : 10 799 773 lignes) :** Rust (~129.95s) < Polars (~478.43s) < Pandas (*N/A*)

### Décision Finale : Choix de FastAPI + Polars (Python)
Bien que le prototype bas niveau en Rust ait prouvé sa supériorité brute sur le plan chronométrique (3.6x plus rapide que Polars sur 1 Go de données), l'architecture Polars en Python a été retenue pour la production. Ce choix repose sur un arbitrage pragmatique entre performance pure et contraintes opérationnelles :

1. **Fiabilité de la Stack d'Observabilité :** L'implémentation Rust a rencontré des limites strictes lors de l'intégration avec la stack de tracing SigNoz. Les particularités des couches réseau Docker, combinées aux configurations gRPC/HTTP (4317/4318) d'OpenTelemetry en Rust, provoquaient des ruptures de communication et empêchaient la bonne remontée des signaux dans l'interface de monitoring.
2. **Gestion du Temps (Time-to-Market) :** Face aux délais impartis pour finaliser le challenge, le débogage des couches de transport asynchrones en Rust a été écarté au profit d'une solution immédiatement fonctionnelle et stable.
3. **Robustesse et Maintenabilité :** Le couple FastAPI + Polars unifie l'écosystème de l'application. Polars, s'appuyant sur des structures en colonnes Apache Arrow, garantit un traitement hautement parallélisé qui consomme l'intégralité du CPU disponible et traite le fichier massif sans aucun crash, offrant le parfait équilibre entre vélocité d'exécution et intégration cloud-native réussie.

---

## 🚀 Instructions de Démarrage en Local (Docker & SigNoz)

L'environnement de développement local fait communiquer l'API, le stockage MinIO et la pile d'observabilité SigNoz à travers un réseau Docker partagé (`signoz-network`).

### 1. execution de signoz via foundry

####  Installation de foundryctl

```bash
curl -fsSL https://signoz.io/foundry.sh | bash
```

#### Deploiement
```bash
foundryctl cast -f casting.yaml
```

### 2. Lancement des conteneurs applicatifs
Pour démarrer simultanément l'API et le stockage MinIO :
```bash
docker compose up -d
```

#### test avec les interfaces graphique:

|**Signoz**|**FastAPI**| **MinIO** | 
| :--: | :--: | :--: |
| http://adresse_ip:8080 | http://adresse_ip:8000 *login: minioadmin & password: minioadminpassword* | http://adresse_ip:9001 | 



### 3. Installation step by step en cas d'erreur avec script foundryctl automatisé (Foundry)
L'installation de SigNoz est orchestrée par l'utilitaire `foundryctl`. En cas de plantage des scripts d'initialisation utilisateur et/ou Timeout après plus de 5 minutes, utiliser l'installation step by step :
```bash
# Validate prerequisites
foundryctl gauge -f casting.yaml

# Generate compose files
foundryctl forge -f casting.yaml

# Start the stack
cd pours/deployment && docker compose up -d
```
### 💾 Initialisation des Données & Création des Buckets (Seeding)

Le projet utilise le script `api/src/seed_data.py` (dont il faudra adapter le *CSV_FILE_PATH = "chemin/fichier.csv*") pour vérifier l'existence des compartiments de stockage nécessaires (`raw` et `processeddata`), les créer si besoin, et y téléverser un fichier CSV initial pour les tests.

###  En Développement Local
Exécutez simplement le script depuis votre machine hôte après avoir démarré vos conteneurs. Les connexions basculeront automatiquement sur `localhost` :
```bash
python api/src/seed_data.py
```

### En Production sur Kubernetes
Le déploiement intègre un **Job Kubernetes** autonome qui s'exécute directement à l'intérieur du cluster et communique avec MinIO via les adresses réseaux isolées.

Pour provisionner les buckets et injecter les fichiers de tests en production :
```bash
kubectl apply -f k8s/seed-job.yaml
```
Vous pouvez suivre l'avancement du téléversement dans les logs du cluster avec :
```bash
kubectl logs job/arteci-api-seeder
```


### 4. (Optionnel) Test du benchmark Pandas vs Polars vs Rust

#### Compilation et lancement de l'API RUST en mode performance
**Installer Cargo si nécessaire**
*Mettre à jour le système et installer les prérequis*
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install build-essential curl -y
```
* *Télécharger et exécuter le script Rustup*
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```
* *Configurer les variables d'environnement*
```bash
source "$HOME/.cargo/env"
```
* *Vérifier que tout est fonctionnel*
```bash
cargo --version
rustc --version
```
**Demarrage de l'api RUST (Axum)**
```bash
# aller dans le repertoire rust-parser
cd rust-parser

cargo build --release

cargo run --release
```
**execution du script de benchmark** *s'assurer que pandas/polars(FastAPI) sont en cours d'exécution*
```bash
python benchmarks/bench_pandas_polars_rust.py nom_fichier.csv
```



### 4. Utilisation de l'API & Tracing
L'interface de l'API est accessible sur `http://localhost:8000/docs` et l'interface SigNoz sur `http://localhost:8080`.

🎯 **Extraction des colonnes :**
```bash
curl -X GET "http://localhost:8000/columns?bucket=raw&file=lst_of_users_anon_1.csv"
```

🎯 **Traitement de normalisation :**
Chaque appel à cette route génère des traces OpenTelemetry envoyées automatiquement au collecteur de SigNoz :
```bash
curl -X POST "http://localhost:8000/processDate" \
     -H "Content-Type: application/json" \
     -d '{
       "bucket": "raw",
       "file": "lst_of_users_anon_1.csv",
       "date_columns": ["DATE_CREATION"],
       "date_formats": ["MDY"],
       "engine": "polars"
     }'
```

---

## ☸️ Déploiement Kubernetes (K8s)

L'infrastructure Kubernetes sépare l'application en modules isolés communicant via le réseau global du cluster (Cross-Namespace DNS). Les manifestes se trouvent dans le dossier `k8s/`.

### 1. Déploiement de SigNoz via Helm
En production, SigNoz s'installe via son gestionnaire de paquets officiel Helm dans son propre espace sécurisé :
```bash
# 1. Ajouter le dépôt de logiciels officiel de SigNoz
helm repo add signoz https://charts.signoz.io

# Mettre à jour vos dépôts Helm locaux pour récupérer la liste des versions
helm repo update

# 2. Installer MicroK8s
sudo snap install microk8s --classic

# 3. Ajouter votre utilisateur au groupe microk8s pour éviter d'utiliser 'sudo' à chaque fois
sudo usermod -a -G microk8s $USER
mkdir -p ~/.kube
chmod 0700 ~/.kube

# /!\ IMPORTANT : Déconnectez-vous et reconnectez-vous à votre session EC2 ici pour appliquer les groupes

# 4. Générer le fichier de configuration pour que 'kubectl' s'y connecte
microk8s config > ~/.kube/config

# 5. Activer le support DNS (indispensable pour SigNoz)
microk8s enable dns

# 6. Créer l'espace isolé (namespace) nommé 'signoz'
kubectl create namespace signoz

# 7. Installer toute l'infrastructure complète SigNoz
helm install my-release signoz/signoz -n signoz
```

### 2. Déploiement de l'API et de MinIO
Pour déployer vos fichiers de configuration, appliquez les manifestes. L'API intègre un `initContainer` de sécurité qui met l'application en pause tant que le serveur de stockage MinIO n'est pas prêt sur le port 9000 :
```bash
kubectl apply -f k8s/
```

### 3. Accès aux interfaces du cluster
Pour ouvrir l'interface de SigNoz sur votre machine de développement locale depuis le cluster Kubernetes, utilisez une redirection de port :
```bash
kubectl port-forward -n signoz svc/my-release-signoz-frontend 3301:3301
```
L'interface web de supervision sera instantanément disponible sur `http://localhost:3301`.
