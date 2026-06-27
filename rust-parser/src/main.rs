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
use chrono::{DateTime, NaiveDate, NaiveDateTime, NaiveTime};
use aws_sdk_s3::{Client, config::{Credentials, Region}};

// Importation du trait nécessaire pour configurer le endpoint OTLP gRPC
use opentelemetry_otlp::WithExportConfig;

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
    // Ajout du champ pour retourner l'aperçu des données sous forme de tableau d'objets JSON
    #[serde(skip_serializing_if = "Option::is_none")]
    preview: Option<serde_json::Value>,
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
    let cleaned = raw.replace('Z', "+00:00");

    if let Ok(dt) = DateTime::parse_from_str(&cleaned, "%Y-%m-%dT%H:%M:%S%.f%z") {
        return Some(dt.naive_utc());
    }
    if let Ok(dt) = DateTime::parse_from_str(&cleaned, "%Y-%m-%dT%H:%M:%S%z") {
        return Some(dt.naive_utc());
    }
    if let Ok(dt) = NaiveDateTime::parse_from_str(raw, "%Y-%m-%dT%H:%M:%S") {
        return Some(dt);
    }

    if let Ok(dt) = NaiveDateTime::parse_from_str(raw, "%Y-%m-%d %H:%M:%S") {
        return Some(dt);
    }
    if let Ok(d) = NaiveDate::parse_from_str(raw, "%Y-%m-%d") {
        return Some(d.and_time(NaiveTime::from_hms_opt(0, 0, 0).unwrap()));
    }
    if let Ok(d) = NaiveDate::parse_from_str(raw, "%Y%m%d") {
        return Some(d.and_time(NaiveTime::from_hms_opt(0, 0, 0).unwrap()));
    }

    None
}

/// Priorité 6 : Mois textuels FR ou EN (ex: "22 mars 2024", "Mar 22 2024")
fn try_parse_textual_month(raw: &str) -> Option<NaiveDateTime> {
    let lower = raw.to_lowercase();

    let nums: Vec<u32> = raw
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse().ok())
        .collect();

    let time_parts: Vec<u32> = {
        let mut found = Vec::new();
        let parts: Vec<&str> = raw.split(':').collect();
        if parts.len() >= 3 {
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
    let normalized = raw.replace('-', "/").replace('.', "/");

    let parts: Vec<u32> = normalized
        .split(|c: char| !c.is_ascii_digit())
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse().ok())
        .collect();

    if parts.len() < 3 {
        return None;
    }

    let (p1, p2, p3) = (parts[0], parts[1], parts[2]);

    let (h, m, s) = if parts.len() >= 6 {
        (parts[3], parts[4], parts[5])
    } else {
        (0, 0, 0)
    };

    let (year, month, day) = {
        if p1 > 12 && p2 <= 12 {
            (p3 as i32, p2, p1)
        }
        else if p2 > 12 && p1 <= 12 {
            (p3 as i32, p1, p2)
        }
        else if hint == "DMY" {
            (p3 as i32, p2, p1)
        } else {
            (p3 as i32, p1, p2)
        }
    };

    let date = NaiveDate::from_ymd_opt(year, month, day)?;
    let time = NaiveTime::from_hms_opt(h, m, s)?;
    Some(date.and_time(time))
}

/// Parseur principal — 8 priorités, aligné sur date_parser.py
pub fn parse_date_cell(value: &str, hint: &str) -> String {
    let raw = value.trim();

    if raw.is_empty() || matches!(raw.to_lowercase().as_str(), "nan" | "null" | "none") {
        return String::new();
    }

    if let Some(dt) = try_parse_unix(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    if let Some(dt) = try_parse_iso(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    if let Some(dt) = try_parse_textual_month(raw) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

    if let Some(dt) = try_parse_ambiguous(raw, hint) {
        return dt.format(OUTPUT_FORMAT).to_string();
    }

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

    // On cherche si une URL spécifique est fournie par Docker, sinon on met l'adresse du réseau Docker
    let minio_endpoint = std::env::var("MINIO_ENDPOINT_URL")
        .unwrap_or_else(|_| "http://minio:9000".to_string()); // "minio" à la place de "localhost"

    let config = aws_config::defaults(aws_config::BehaviorVersion::latest())
        .credentials_provider(credentials)
        .region(Region::new("us-east-1"))
        .endpoint_url(minio_endpoint)
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

use tracing::instrument;

#[instrument(name = "process_date_handler", skip(payload))]
async fn process_date_handler(
    Json(payload): Json<ProcessDateRequest>,
) -> impl IntoResponse {
    if payload.date_columns.len() != payload.date_formats.len() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ProcessResponse {
                status: "error".to_string(),
                engine_used: "rust".to_string(),
                message: "Les listes 'date_columns' et 'date_formats' doivent avoir la même taille.".to_string(),
                preview: None,
            }),
        );
    }

    let s3_client = get_minio_client().await;

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
                    preview: None,
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
                    preview: None,
                }),
            );
        }
    };

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
                    preview: None,
                }),
            );
        }
    };

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
                        preview: None,
                    }),
                );
            }
        }
    }

    let mut writer = csv::WriterBuilder::new()
        .delimiter(b';')
        .from_writer(vec![]);

    if writer.write_record(&headers).is_err() {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse {
            status: "error".into(), engine_used: "rust".into(),
            message: "Erreur d'écriture des en-têtes.".into(),
            preview: None,
        }));
    }

    for result in reader.records() {
        let record = match result {
            Ok(r) => r,
            Err(_) => continue,
        };

        let new_record: Vec<String> = record
            .iter()
            .enumerate()
            .map(|(idx, field)| {
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
                preview: None,
            }));
        }
    }

    // 1. Finalisation du tampon CSV (Fin de ta boucle d'écriture)
    let final_csv_bytes: Vec<u8> = match writer.into_inner() {
        Ok(bytes) => bytes,
        Err(_) => return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ProcessResponse {
                status: "error".to_string(),
                engine_used: "rust".to_string(),
                message: "Erreur de finalisation du tampon CSV.".to_string(),
                preview: None, // Initialisation obligatoire du nouveau champ
            }),
        ),
    };

    // 2. Envoi du fichier traité dans le bucket 'processeddata'
    let body_stream = aws_sdk_s3::primitives::ByteStream::from(final_csv_bytes.clone());
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
                preview: None, // Initialisation obligatoire du nouveau champ
            }),
        );
    }

    // 3. Extraction des 100 premières lignes pour le retour JSON
    // Utilisation explicite de std::io::Cursor pour éviter tout problème d'import manquants
    let mut preview_reader = csv::ReaderBuilder::new()
        .delimiter(b';')
        .from_reader(std::io::Cursor::new(final_csv_bytes));

    let preview_headers = preview_reader.headers().unwrap().clone();
    let mut preview_rows = Vec::new();

    for result in preview_reader.records().take(100) {
        if let Ok(record) = result {
            let mut row_map = serde_json::Map::new();
            for (header, field) in preview_headers.iter().zip(record.iter()) {
                row_map.insert(header.to_string(), serde_json::Value::String(field.to_string()));
            }
            preview_rows.push(serde_json::Value::Object(row_map));
        }
    }

    // 4. Réponse HTTP 200 de succès avec l'aperçu complet des données
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
            preview: Some(serde_json::Value::Array(preview_rows)),
        }),
    )
}

// ---------------------------------------------------------------------------
// INITIALISATION DE TÉLÉMÉTRIE (SIGNOZ OTLP gRPC - Version Robuste)
// ---------------------------------------------------------------------------

fn init_telemetry() -> opentelemetry_sdk::trace::Tracer {
    use tracing_subscriber::layer::SubscriberExt;
    use tracing_subscriber::util::SubscriberInitExt;

    // Récupération de l'endpoint fourni par Docker Compose
    let otlp_endpoint = std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
        .unwrap_or_else(|_| "http://signoz-ingester-1:4317".to_string());

    // Configuration de l'exportateur gRPC pour OpenTelemetry
    let exporter = opentelemetry_otlp::new_exporter()
        .tonic()
        .with_endpoint(otlp_endpoint);

    // Initialisation du pipeline de traces
    let tracer = opentelemetry_otlp::new_pipeline()
        .tracing()
        .with_exporter(exporter)
        .with_trace_config(
            opentelemetry_sdk::trace::config().with_resource(
                opentelemetry_sdk::Resource::new(vec![opentelemetry::KeyValue::new(
                    "service.name",
                    "arteci-api-rust", // C'est ce nom que tu chercheras dans SigNoz
                )]),
            ),
        )
        .install_batch(opentelemetry_sdk::runtime::Tokio)
        .expect("Impossible d'initialiser le pipeline OpenTelemetry");

    // Création de la couche de télémétrie pour le framework Tracing
    let telemetry_layer = tracing_opentelemetry::layer().with_tracer(tracer.clone());
    
    // Initialisation sécurisée du registre global (on ignore si déjà initialisé)
    let _ = tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .with(tracing_subscriber::fmt::layer())
        .with(telemetry_layer)
        .try_init();

    tracer
}

// ---------------------------------------------------------------------------
// POINT D'ENTRÉE — ÉCOUTE SUR 0.0.0.0
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() {
    let _tracer = init_telemetry();

    let app = Router::new()
        .route("/processDate", post(process_date_handler))
        .layer(tower_http::trace::TraceLayer::new_for_http());

    let addr = SocketAddr::from(([0, 0, 0, 0], 8001));
    tracing::info!("Serveur Rust API démarré sur {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}