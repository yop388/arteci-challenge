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

### Synthèse des Résultats de test en Local

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
| http://adresse_ip:8080 | http://adresse_ip:8000/docs | http://adresse_ip:9001 `login: minioadmin & password: minioadminpassword` | 



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
* **Demarrage de l'api RUST (Axum)**
```bash
# aller dans le repertoire rust-parser
cd rust-parser

cargo build --release
```
```bash
export MINIO_ENDPOINT_URL=http://localhost:9000

cargo run --release
```
* **execution du script de benchmark** *s'assurer que pandas/polars(FastAPI) sont en cours d'exécution*

```bash
python benchmarks/bench_pandas_polars_rust.py nom_fichier.csv
```

### 5. Utilisation de l'API & Tracing

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

## ☸️ Déploiement Kubernetes (K8s) `Nous utilisons au choix un AWS EC2 m7i-flex.large pour les tests de deployement `

L'infrastructure Kubernetes sépare l'application en modules isolés communicant via le réseau global du cluster (Cross-Namespace DNS). Les manifestes se trouvent dans le dossier `k8s/`.

### 1. Installation des prérequis
En production, SigNoz s'installe via son gestionnaire de paquets officiel Helm dans son propre espace sécurisé :

* **Mettre à jour l'index des paquets et installer les prérequis**
```bash
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg
```

* **Télécharger la clé de signature publique du dépôt Kubernetes**
```bash
# Crée le dossier pour les clés si nécessaire
sudo mkdir -p -m 755 /etc/apt/keyrings

# Télécharge la clé
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.30/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
sudo chmod 644 /etc/apt/keyrings/kubernetes-apt-keyring.gpg
```

* **Ajouter le dépôt APT de Kubernetes**
```bash
echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.30/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list
sudo chmod 644 /etc/apt/sources.list.d/kubernetes.list
```

* **Installer kubectl**
```bash
sudo apt-get update
sudo apt-get install -y kubectl
```

* **Vérifier l'installation**
```bash
kubectl version --client
```

* **Installer helm : Importer la clé GPG de Helm**
```bash
curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey | gpg --dearmor | sudo tee /etc/apt/keyrings/helm.gpg > /dev/null
```

* **Ajouter le dépôt Helm à au sources**
```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" | sudo tee /etc/apt/sources.list.d/helm-stable-debian.list
```

* **Installer Helm**
```bash
sudo apt-get update
sudo apt-get install -y helm
```
* **Vérifier l'installation**
```bash
helm version
```

### 2. Déploiement de SigNoz via Helm 
* **Partie 1 : Installation et Configuration du Cluster (MicroK8s)**
```bash
# 1. Installer MicroK8s sur l'instance EC2
sudo snap install microk8s --classic

# 2. Configurer les permissions utilisateur (évite d'utiliser 'sudo' devant kubectl/microk8s)
sudo usermod -a -G microk8s $USER
mkdir -p ~/.kube
chmod 0700 ~/.kube

# 3. Générer le fichier de configuration pour que 'kubectl' local s'y connecte
microk8s config > ~/.kube/config

# /!\ IMPORTANT : Se déconnecter et  se reconnecter à la session SSH EC2 ici pour que l'ajout au groupe microk8s soit pris en compte.

# 4. Activer les addons indispensables (DNS et Stockage Persistant)
microk8s enable dns
microk8s enable hostpath-storage
```
* **Partie 2 : Déploiement de SigNoz**
```bash
# 5. Ajouter le dépôt de logiciels officiel de SigNoz (URL Corrigée)
helm repo add signoz https://charts.signoz.io

# 6. Mettre à jour les dépôts Helm locaux
helm repo update

# 7. Créer l'espace isolé (namespace) nommé 'signoz'
kubectl create namespace signoz

# 8. Installer toute l'infrastructure complète SigNoz
helm install my-release signoz/signoz -n signoz
```
* **Partie 3 : Exposition sur Internet (Accès Web)**
```bash
# 9. Modifier le service pour l'exposer via un NodePort fixe
kubectl patch svc my-release-signoz -n signoz -p '{"spec": {"type": "NodePort"}}'

# 10. Récupérer le port externe attribué à l'interface web (ex: 8080:30528/TCP)
kubectl get svc my-release-signoz -n signoz
```

### (Optionnel) quelques commandes de vérification importantes
* **`1. Vérifier l'état de l'instance (Le Nœud)`**
```bash
# Lister le nœud et vérifier que le statut est "Ready"
kubectl get nodes
```
* **`2. Vérifier l'état des Pods SigNoz`**
```bash
# Voir tous les pods dans le namespace 'signoz'
kubectl get pods -n signoz

# Voir les pods avec plus de détails (comme l'adresse IP interne ou le nœud)
kubectl get pods -n signoz -o wide

# Suivre en temps réel les changements de statut (pratique lors d'un déploiement)
kubectl get pods -n signoz --watch
```
* **`3. En cas de problème (Le diagnostic de base)`**
```bash
# 1. Voir l'historique des événements d'un pod (pour comprendre POURQUOI il bloque)
kubectl describe pod <nom-du-pod> -n signoz

# 2. Voir les logs/journaux système du conteneur (pour voir l'erreur de l'application)
kubectl logs <nom-du-pod> -n signoz
```


### 2. Déploiement de l'API et de MinIO
Grâce à l'activation préalable du stockage persistant (hostpath-storage) sur MicroK8s, le volume demandé par MinIO (minio-pvc de 5Go) va pouvoir s'associer instantanément.

Pour déployer l'ensemble de l'application `(le serveur de stockage, le seeder de données et l'API)`, appliquer tous les manifestes du dossier d'un seul coup
```bash
# Appliquer tous les fichiers YAML du dossier k8s
kubectl apply -f k8s/
```
**Note sur la sécurité :** L'API intègre un initContainer nommé wait-for-minio. Il met automatiquement l'API en pause et attend que le conteneur MinIO soit totalement prêt et réponde sur le port 9000 avant de démarrer l'application.

* **suivre le bon démarrage de votre API et de MinIO**
```bash
kubectl get pods --watch
```

### 3. Accès aux interfaces et à l'API depuis Internet
* **s'assurrer que l'interface graphique de Minio est aussi accessible depuis internet allors :**
```bash
#Modifier le type de service via le terminal
kubectl patch svc minio -p '{"spec": {"type": "NodePort"}}'
```
```bash
#Récupérer les ports externes attribués grace à la commande suivante pour voir quels ports aléatoires Kubernetes a généré
kubectl get svc minio
```
```
exemple de sortie :

:~$ kubectl get svc minio
NAME    TYPE       CLUSTER-IP      EXTERNAL-IP   PORT(S)                         AGE
minio   NodePort   10.152.183.31   <none>        9000:30163/TCP,9001:31271/TCP   23m
```
|**Tableau de bord de supervision (SigNoz)**|**API (arteci-api-service)**|**Tableau de bord de MinIO**|
| :--- |:--- |:--- |
|URL d'accès : **`http://adresse_ip:30528`**|URL d'accès : **`http://adresse_ip:30080/docs`**|URL d'accès : **`http://adresse_ip:31271`** Access Key (User) : **`minioadmin`** & Secret Key (Password) : **`minioadminpassword`**|

* **`NB: pour acceder depuis internet, s'assurer de la configuration du Security Group AWS de l'instance EC2 et ajoutez une règle entrante (Custom TCP) pour autoriser le ports externes`**
