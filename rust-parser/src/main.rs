use axum::{
    routing::post,
    extract::Json,
    http::StatusCode,
    response::IntoResponse,
    Router,
};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;
use std::io::Cursor;
use chrono::{DateTime, NaiveDate, NaiveDateTime, NaiveTime, TimeZone, Utc};
use aws_sdk_s3::{Client, config::{Credentials, Region}};

// ---------------------------------------------------------------------------
// STRUCTURES DE DONNÉES
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct ProcessDateRequest {
    bucket: String,
    file: String,
    date_columns: Vec<String>,
    date_formats: Vec<String>,
}

#[derive(Serialize)]
struct ProcessResponse {
    status: String,
    engine_used: String,
    message: String,
}

// ---------------------------------------------------------------------------
// DONNÉES STATIQUES : MOIS TEXTUELS FR / EN
// ---------------------------------------------------------------------------

const MOIS_FR: &[&str] = &[
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
];

const MOIS_EN_ABBR: &[&str] = &[
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
];

const OUTPUT_FORMAT: &str = "%d-%m-%Y %H:%M:%S";

// ---------------------------------------------------------------------------
// PARSEUR — 8 PRIORITÉS (aligné sur date_parser.py)
// ---------------------------------------------------------------------------

/// Priorité 1 : Timestamp Unix (secondes ou millisecondes)
fn try_parse_unix(raw: &str) -> Option<NaiveDateTime> {
    if raw.chars().all(|c| c.is_ascii_digit()) && raw.len() >= 10 {
        if let Ok(ts) = raw.parse::<i64>() {
            let secs = if ts > 5_000_000_000 { ts / 1000 } else { ts };
            return DateTime::from_timestamp(secs, 0)
                .map(|dt| dt.naive_utc());
        }
    }
    None
}

/// Priorités 2, 3, 4, 5 : Formats ISO et dérivés non ambigus
fn try_parse_iso(raw: &str) -> Option<NaiveDateTime> {
    // Priorité 2a : ISO 8601 avec timezone 'Z'  → remplacé par +00:00
    let cleaned = raw.replace('Z', "+00:00");

    // Priorité 2b : avec millisecondes + timezone  ex: 2024-03-22T14:30:00.123+00:00
    if let Ok(dt) = DateTime::parse_from_str(&cleaned, "%Y-%m-%dT%H:%M:%S%.f%z") {
        return Some(dt.naive_utc());
    }
    // Priorité 2c : sans millisecondes + timezone  ex: 2024-03-22T14:30:00+00:00
    if let Ok(dt) = DateTime::parse_from_str(&cleaned, "%Y-%m-%dT%H:%M:%S%z") {
        return Some(dt.naive_utc());
    }
    // Priorité 2d : ISO sans timezone  ex: 2024-03-22T14:30:00
    if let Ok(dt) = NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S") {
        return Some(dt);
    }

    // Priorité 3 : yyyy-MM-dd HH:mm:ss
    if let Ok(dt) = NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S") {
        return Some(dt);
    }
    // Priorité 4 : yyyy-MM-dd
    if let Ok(d) = NaiveDate::parse_from_str(raw, "%Y-%m-%d") {
        return Some(d.and_time(NaiveTime::from_hms_opt(0, 0, 0).unwrap()));
    }
    // Priorité 5 : yyyyMMdd (ISO compact)
    if let Ok(d) = NaiveDate::parse_from_str(raw, "%Y%m%d") {
        return Some(d.and_time(NaiveTime::from_hms_opt(0, 0, 0).unwrap()));
    }

    None
}

/// Priorité 6 : Mois textuels FR ou EN (ex: "22 mars 2024", "Mar 22 2024")
fn try_parse_textual_month(raw: &str) -> Option<NaiveDateTime> {
    let lower = raw.to_lowercase();

    // Extraction générique des nombres présents dans la chaîne
    let nums: Vec<u32> = raw
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse().ok())
        .collect();

    // Extraction de l'heure si présente sous la forme HH:MM:SS
    let time_parts: Vec<u32> = {
        let mut found = Vec::new();
        let parts: Vec<&str> = raw.split(':').collect();
        if parts.len() >= 3 {
            // On cherche trois groupes numériques consécutifs séparés par ':'
            for i in 0..parts.len().saturating_sub(2) {
                let h_raw: Vec<&str> = parts[i].split_whitespace().collect();
                let m_raw = parts[i + 1];
                let s_raw: Vec<&str> = parts[i + 2].split_whitespace().collect();
                if let (Some(h), Some(m), Some(s)) = (
                    h_raw.last().and_then(|v| v.parse().ok()),
                    m_raw.parse().ok(),
                    s_raw.first().and_then(|v| v.parse().ok()),
                ) {
                    found = vec![h, m, s];
                    break;
                }
            }
        }
        found
    };

    let (h, m, s) = if time_parts.len() == 3 {
        (time_parts[0], time_parts[1], time_parts[2])
    } else {
        (0, 0, 0)
    };

    // Test mois français
    for (i, &mois) in MOIS_FR.iter().enumerate() {
        if lower.contains(mois) && nums.len() >= 2 {
            let jour = nums[0];
            let annee = nums[1];
            let month = (i + 1) as u32;
            if let Some(date) = NaiveDate::from_ymd_opt(annee as i32, month, jour) {
                if let Some(time) = NaiveTime::from_hms_opt(h, m, s) {
                    return Some(date.and_time(time));
                }
            }
        }
    }

    // Test mois anglais abrégés
    for (i, &mois) in MOIS_EN_ABBR.iter().enumerate() {
        if lower.contains(mois) && nums.len() >= 2 {
            let jour = nums[0];
            let annee = nums[1];
            let month = (i + 1) as u32;
            if let Some(date) = NaiveDate::from_ymd_opt(annee as i32, month, jour) {
                if let Some(time) = NaiveTime::from_hms_opt(h, m, s) {
                    return Some(date.and_time(time));
                }
            }
        }
    }

    None
}

/// Priorités 7 & 8 : Formats ambigus (/ - .) avec règles d'ambiguïté + extraction d'heure
fn try_parse_ambiguous(raw: &str, hint: &str) -> Option<NaiveDateTime> {
    // Normalisation : on remplace tous les séparateurs de date par '/'
    let normalized = raw.replace('-', "/").replace('.', "/");

    // Extraction de tous les blocs numériques
    let parts: Vec<u32> = normalized
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse().ok())
        .collect();

    if parts.len() < 3 {
        return None;
    }

    let (p1, p2, p3) = (parts[0], parts[1], parts[2]);

    // Extraction de l'heure si présente (Priorité 8 : blocs 3, 4, 5)
    let (h, m, s) = if parts.len() >= 6 {
        (parts[3], parts[4], parts[5])
    } else {
        (0, 0, 0)
    };

    // Construction de la date selon les règles d'ambiguïté
    let (year, month, day) = {
        // RÈGLE 1 : p1 > 12 → jour certain → format DMY
        if p1 > 12 && p2 <= 12 {
            (p3 as i32, p2, p1)
        }
        // RÈGLE 2 : p2 > 12 → mois certain → format MDY
        else if p2 > 12 && p1 <= 12 {
            (p3 as i32, p1, p2)
        }
        // RÈGLE 3 : ambiguïté totale → application stricte du hint
        else if hint == "DMY" {
            (p3 as i32, p2, p1) // JJ/MM/AAAA
        } else {
            (p3 as i32, p1, p2) // MM/JJ/AAAA
        }
    };

    let date = NaiveDate::from_ymd_opt(year, month, day)?;
    let time = NaiveTime::from_hms_opt(h, m, s)?;
    Some(date.and_time(time))
}

/// Parseur principal — 8 priorités, aligné sur date_parser.py
pub fn parse_date_cell(value: &str, hint: &str) -> String {
    let raw = value.trim();

    // Cellule vide / nulle
    if raw.is_empty() || matches!(raw.to_lowercase().as_str(), "nan" | "null" | "none") {
        return String::new();
    }

    // Priorité 1 : Timestamp Unix
    if let Some(dt) = try_parse_unix(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    // Priorités 2, 3, 4, 5 : Formats ISO
    if let Some(dt) = try_parse_iso(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    // Priorité 6 : Mois textuels FR / EN
    if let Some(dt) = try_parse_textual_month(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    // Priorités 7 & 8 : Formats ambigus + heure
    if let Some(dt) = try_parse_ambiguous(raw, hint) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    // Cellule non parsable : retour de la valeur d'origine inchangée
    value.to_string()
}

// ---------------------------------------------------------------------------
// CLIENT MINIO
// ---------------------------------------------------------------------------

async fn get_minio_client() -> Client {
    let credentials = Credentials::new(
        "minioadmin",
        "minioadminpassword",
        None,
        None,
        "manual",
    );

    let config = aws_config::defaults(aws_config::BehaviorVersion::latest())
        .credentials_provider(credentials)
        .region(Region::new("us-east-1"))
        .endpoint_url("http://localhost:9000")
        .load()
        .await;

    let s3_config = aws_sdk_s3::config::Builder::from(&config)
        .force_path_style(true)
        .build();

    Client::from_conf(s3_config)
}

// ---------------------------------------------------------------------------
// HANDLER HTTP
// ---------------------------------------------------------------------------

async fn process_date_handler(
    Json(payload): Json<ProcessDateRequest>,
) -> impl IntoResponse {
    // Validation : listes de même taille
    if payload.date_columns.len() != payload.date_formats.len() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ProcessResponse {
                status: "error".to_string(),
                engine_used: "rust".to_string(),
                message: "Les listes 'date_columns' et 'date_formats' doivent avoir la même taille.".to_string(),
            }),
        );
    }

    let s3_client = get_minio_client().await;

    // 1. Récupération du fichier source depuis MinIO
    let s3_object = match s3_client
        .get_object()
        .bucket(&payload.bucket)
        .key(&payload.file)
        .send()
        .await
    {
        Ok(output) => output,
        Err(err) => {
            return (
                StatusCode::NOT_FOUND,
                Json(ProcessResponse {
                    status: "error".to_string(),
                    engine_used: "rust".to_string(),
                    message: format!("Fichier ou Bucket introuvable : {}", err),
                }),
            );
        }
    };

    let data_bytes = match s3_object.body.collect().await {
        Ok(bytes) => bytes.to_vec(),
        Err(err) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(ProcessResponse {
                    status: "error".to_string(),
                    engine_used: "rust".to_string(),
                    message: format!("Erreur lors de la lecture du flux S3 : {}", err),
                }),
            );
        }
    };

    // 2. Lecture du CSV (séparateur ';')
    let mut reader = csv::ReaderBuilder::new()
        .delimiter(b';')
        .from_reader(Cursor::new(data_bytes));

    let headers = match reader.headers() {
        Ok(h) => h.clone(),
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(ProcessResponse {
                    status: "error".to_string(),
                    engine_used: "rust".to_string(),
                    message: "Impossible de lire les en-têtes du CSV.".to_string(),
                }),
            );
        }
    };

    // Résolution des index des colonnes à traiter
    let mut target_indices: Vec<(usize, &str)> = Vec::new();
    for (col, fmt) in payload.date_columns.iter().zip(payload.date_formats.iter()) {
        match headers.iter().position(|h| h == col) {
            Some(pos) => target_indices.push((pos, fmt.as_str())),
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(ProcessResponse {
                        status: "error".to_string(),
                        engine_used: "rust".to_string(),
                        message: format!("Colonne '{}' absente du fichier CSV.", col),
                    }),
                );
            }
        }
    }

    // 3. Traitement ligne par ligne avec écriture dans le CSV de sortie
    let mut writer = csv::WriterBuilder::new()
        .delimiter(b';')
        .from_writer(vec![]);

    if writer.write_record(&headers).is_err() {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse {
            status: "error".into(), engine_used: "rust".into(),
            message: "Erreur d'écriture des en-têtes.".into(),
        }));
    }

    for result in reader.records() {
        let record = match result {
            Ok(r) => r,
            Err(_) => continue, // Ligne corrompue : ignorée sans bloquer le traitement
        };

        let new_record: Vec<String> = record
            .iter()
            .enumerate()
            .map(|(idx, field)| {
                // Si la colonne courante est une colonne de date à traiter
                if let Some(&(_, hint)) = target_indices.iter().find(|(i, _)| *i == idx) {
                    parse_date_cell(field, hint)
                } else {
                    field.to_string()
                }
            })
            .collect();

        if writer.write_record(&new_record).is_err() {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse {
                status: "error".into(), engine_used: "rust".into(),
                message: "Erreur d'écriture d'une ligne.".into(),
            }));
        }
    }

    let final_csv_bytes = match writer.into_inner() {
        Ok(bytes) => bytes,
        Err(_) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse {
            status: "error".into(), engine_used: "rust".into(),
            message: "Erreur de finalisation du tampon CSV.".into(),
        })),
    };

    // 4. Sauvegarde dans le bucket "processeddata"
    let body_stream = aws_sdk_s3::primitives::ByteStream::from(final_csv_bytes);
    if let Err(err) = s3_client
        .put_object()
        .bucket("processeddata")
        .key(&payload.file)
        .body(body_stream)
        .send()
        .await
    {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ProcessResponse {
                status: "error".to_string(),
                engine_used: "rust".to_string(),
                message: format!("Erreur d'écriture dans MinIO : {}", err),
            }),
        );
    }

    (
        StatusCode::OK,
        Json(ProcessResponse {
            status: "success".to_string(),
            engine_used: "rust".to_string(),
            message: format!(
                "Traitement complété avec succès ({} colonnes normalisées) pour le fichier '{}'.",
                payload.date_columns.len(),
                payload.file
            ),
        }),
    )
}

// ---------------------------------------------------------------------------
// POINT D'ENTRÉE
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let app = Router::new().route("/processDate", post(process_date_handler));

    // Port 8001 pour coexister avec FastAPI (port 8000)
    let addr = SocketAddr::from(([127, 0, 0, 1], 8001));
    tracing::info!("Serveur Rust API démarré sur http://{}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
