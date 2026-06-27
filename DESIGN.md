# Spécifications de Conception Technique - Projet ARTECI

## 1. Reformulation du Problème
L'application interne ARTECI traite des volumes importants de données textuelles (fichiers logs, CSV, Excel). L'étape critique de validation des données subit un fort ralentissement (goulot d'étranglement) à cause de l'hétérogénéité des formats de date. Les fichiers volumineux contiennent des colonnes de dates non standardisées, où coexistent parfois des formats français (DMY) et anglais (MDY) au sein d'une même colonne. 

L'enjeu est de construire une API hautement performante, scalable et cloud-native. Elle doit centraliser et accélérer la normalisation de ces colonnes de dates de façon transparente pour le stockage objet existant.

## 2. Périmètre des Formats de Date Supportés
L'API doit identifier, analyser et convertir de façon unifiée vers le format cible unique : `JJ-MM-AAAA HH:mm:ss`.

Les grandes familles de formats prises en charge incluent :

### Groupe 1 : ISO et timestamps

| Format | Exemple | Remarque |
| :--- | :--- | :--- |
| `yyyy-MM-dd` | 2024-03-22 | Format ISO 8601 de base, non ambigu. |
| `yyyy-MM-dd HH:mm:ss` | 2024-03-22 14:30:00 | ISO avec heure, le plus courant dans les logs. |
| `yyyy-MM-dd'T'HH:mm:ss` | 2024-03-22T14:30:00 | ISO 8601 avec séparateur T (format API/JSON courant). |
| `yyyy-MM-dd'T'HH:mm:ssZ` | 2024-03-22T14:30:00Z | ISO avec timezone UTC. Normaliser en ignorant le fuseau (outputer l'heure telle quelle). |
| `yyyyMMdd` | 20240322 | ISO compact, courant dans les noms de fichiers et logs. |
| Timestamp Unix (entier) | 1711108200 | Secondes depuis epoch. Si > 1e10 : interpréter en millisecondes. |

### Groupe 2 : Formats français (DMY)
| Format | Exemple | Remarque |
| :--- | :--- | :---|
| `dd/MM/yyyy` | 22/03/2024 | Format français standard, séparateur /. |
| `dd-MM-yyyy` | 22-03-2024 | Même format avec tiret. Ambigu si jour <= 12 : se fier à l'indication DMY.|
| `dd.MM.yyyy` | 22.03.2024 | Variante avec point, courante en Europe continentale. |
| `d/M/yyyy` | 2/3/2024 | Sans zéro de padding. Ambigu : se fier à DMY. |
| `dd/MM/yyyy HH:mm:ss` | 22/03/2024 14:30:00 | Format français avec heure.|
| `d MMM yyyy (fr)` | 22 mars 2024 | Nom de mois en français. Non ambigu (le mois est textuel). Gérer les accents (janvier, février...). |

### Groupe 3 : Formats anglo-saxons (MDY)

| Format | Exemple | Remarque |
| :--- | :--- | :--- |
| `MM/dd/yyyy` | 03/22/2024 | Format US standard. Ambigu si jour <= 12 : se fier à MDY. |
| `MM-dd-yyyy` | 03-22-2024 | Variante avec tiret. Ambigu : se fier à MDY. |
| `M/d/yyyy` | 3/2/2024 | Sans padding. Hautement ambigu : TOUJOURS se fier à l'indication utilisateur. |
| `MMM d, yyyy (en)` | Mar 22, 2024 | Nom de mois abrégé en anglais. Non ambigu. |
| `MMMM d, yyyy (en)` | March 22, 2024 | Nom de mois complet en anglais. Non ambigu. |
| `MM/dd/yyyy h:mm:ss a` | 03/22/2024 2:30:00 PM | Format US avec heure AM/PM. Convertir en 24h pour la sortie normalisée. |

*Note métier :* Toute cellule mal formatée ou corrompue ne bloque pas le traitement ; la valeur est renvoyée brute en l'état.


## 3. Contrat d'Interface des Endpoints

### Endpoint 1 : Récupération des colonnes
* **Route :** `GET /columns`
* **Query Params :** `bucket` (string), `file` (string)

### Endpoint 2 : Normalisation des dates
* **Route :** `POST /processDate`
* **Body (JSON) :** Contient le bucket, le fichier cible, les colonnes à traiter et les formats attendus (`DMY`/`MDY`).

## 4. Schéma du Flux de Données

Le flux suit une architecture découplée où l'API manipule directement le stockage (MinIO) :

![Schéma du flux de données](docs/Schema_flux_donnees.png)

## 5. Décision d'Architecture — Choix du Moteur et Arbitrage Technique

À la suite des phases de tests de charge sur un fichier critique de **931 Mo (10,7 millions de lignes)**, un arbitrage architectural majeur a été réalisé entre la performance brute en isolation et l'efficacité opérationnelle globale de la stack. Le choix final s'est porté sur **Polars (Python/FastAPI)** pour l'environnement de production.

### Analyse des Performances Brutes (Stress Tests)

1. **Rust Axum (Moteur Bas Niveau)  ~129.95s [Performances Maximales] :**
   En termes de vitesse pure, l'implémentation native en Rust surpasse largement la concurrence. Malgré une exécution séquentielle qui engendre un fort *Memory Churn* (85% d'occupation RAM) dû aux allocations répétées sur notre architecture matérielle (RAM DDR3 lente), Rust tire pleinement parti de l'absence d'overhead pour valider le fichier en un temps record.
2. **Polars (Optimisation Vectorielle)  ~478.43s :**
   Bien que propulsé par un cœur écrit en Rust et basé sur Apache Arrow, le moteur Polars en Python affiche un temps supérieur sur cette volumétrie spécifique. L'allocation par blocs contigus et la parallélisation native provoquent une surcharge de traitement importante sous de fortes contraintes de bande passante mémoire locale, rendant Polars 3,6 fois plus lent que Rust en isolation.

### Justification du Pivot de Production : Pourquoi opter pour Polars ?

Malgré la supériorité chronométrique de Rust, la solution **Polars + FastAPI** a été sélectionnée pour sécuriser le déploiement en production, motivée par des critères DevOps et des contraintes réelles de livraison :

* **Obstacles Critiques d'Observabilité (Le point de blocage) :** L'intégration de la télémétrie OpenTelemetry au sein de l'écosystème asynchrone Rust vers la stack SigNoz (opérée par `foundryctl`) a présenté des frictions majeures. Les conflits de résolution d'alias DNS internes aux réseaux Docker isolés et les instabilités de routage sur les canaux gRPC (`4317`) et HTTP (`4318`) bloquaient la remontée des traces de manière silencieuse.
* **Maîtrise du Time-to-Market :** Résoudre ces anomalies de communication bas niveau au sein du conteneur Rust demandait un investissement temps disproportionné par rapport au calendrier du challenge.
* **Compromis Idéal d'Ingénierie :** Polars offre une interface Python de haut niveau extrêmement robuste, combinant une syntaxe agile, une intégration réseau nativement stable avec les solutions d'observabilité standard, et des performances qui restent largement industrielles (le fichier de 931 Mo est traité de bout en bout sans crash ni timeout).

### Conclusion
La bascule vers **FastAPI + Polars** incarne une décision d'ingénierie pragmatique : privilégier une stack stable, immédiatement observable, documentée et intégrable, plutôt qu'une performance brute isolée mais aveugle au sein du système.