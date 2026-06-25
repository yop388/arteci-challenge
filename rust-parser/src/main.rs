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
use chrono::NaiveDateTime;
use aws_sdk_s3::{Client, config::{Credentials, Region}};

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

// Fonction de parsing unitaire robuste
fn parse_date_cell(value: &str, hint: &str) -> String {
    let val_trimmed = value.trim();
    if val_trimmed.is_empty() {
        return value.to_string();
    }

    let formats_iso = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"];
    let formats_dmy = ["%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"];
    let formats_mdy = ["%m/%d/%Y %H:%M:%S", "%m-%d-%Y %H:%M:%S", "%m/%d/%Y", "%m-%d-%Y"];

    let mut all_formats = Vec::new();
    all_formats.extend_from_slice(&formats_iso);
    
    if hint == "DMY" {
        all_formats.extend_from_slice(&formats_dmy);
        all_formats.extend_from_slice(&formats_mdy);
    } else {
        all_formats.extend_from_slice(&formats_mdy);
        all_formats.extend_from_slice(&formats_dmy);
    }

    for fmt in all_formats {
        if let Ok(dt) = NaiveDateTime::parse_from_str(val_trimmed, fmt) {
            return dt.format("%d-%m-%Y %H:%M:%S").to_string();
        }
    }

    value.to_string()
}

// Initialisation du client S3 configuré pour MinIO (Version Corrigée)
async fn get_minio_client() -> Client {
    let credentials = Credentials::new(
        "minioadmin",         // Identifiant de ta stack
        "minioadminpassword", // Mot de passe de ta stack
        None,
        None,
        "manual",
    );

    let config = aws_config::defaults(aws_config::BehaviorVersion::latest())
        .credentials_provider(credentials)
        .region(Region::new("us-east-1"))
        .endpoint_url("http://localhost:9000") // URL locale de MinIO
        .load()
        .await;

    // CRUCIAL POUR MINIO LOCAL : Forcer force_path_style à true
    let s3_config = aws_sdk_s3::config::Builder::from(&config)
        .force_path_style(true)
        .build();

    Client::from_conf(s3_config)
}

async fn process_date_handler(
    Json(payload): Json<ProcessDateRequest>,
) -> impl IntoResponse {
    let s3_client = get_minio_client().await;

    // 1. Récupération du fichier depuis le bucket source MinIO
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
                    message: format!("Fichier ou Bucket introuvable: {}", err),
                }),
            );
        }
    };

    // Lecture des octets du fichier en mémoire
    let data_bytes = match s3_object.body.collect().await {
        Ok(bytes) => bytes.to_vec(),
        Err(err) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(ProcessResponse {
                    status: "error".to_string(),
                    engine_used: "rust".to_string(),
                    message: format!("Erreur lors de la lecture du flux : {}", err),
                }),
            );
        }
    };

    // 2. Traitement du CSV avec le séparateur ';' (identique à ton code Python)
    let mut reader = csv::ReaderBuilder::new()
        .delimiter(b';')
        .from_reader(Cursor::new(data_bytes));

    // Récupération des en-têtes pour localiser l'index des colonnes à traiter
    let headers = match reader.headers() {
        Ok(h) => h.clone(),
        Err(_) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(ProcessResponse {
                    status: "error".to_string(),
                    engine_used: "rust".to_string(),
                    message: "Impossible de lire les en-têtes du CSV".to_string(),
                }),
            );
        }
    };

    // Trouver l'index de chaque colonne cible demandée
    let mut target_indices = Vec::new();
    for col in &payload.date_columns {
        if let Some(pos) = headers.iter().position(|h| h == col) {
            target_indices.push(pos);
        } else {
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

    // Préparation de l'écriture du nouveau CSV nettoyé
    let mut writer = csv::WriterBuilder::new()
        .delimiter(b';')
        .from_writer(vec![]);

    // Écrire les en-têtes originaux dans le nouveau fichier
    if writer.write_record(&headers).is_err() {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse { status: "error".into(), engine_used: "rust".into(), message: "Erreur d'écriture".into() }));
    }

    // Itération sur chaque ligne du fichier pour appliquer le parsing à la volée
    for result in reader.records() {
        let record = match result {
            Ok(r) => r,
            Err(_) => continue, // Ignore les lignes corrompues pour ne pas bloquer le traitement complet
        };

        let mut new_record = Vec::new();
        for (idx, field) in record.iter().enumerate() {
            // Si l'index actuel correspond à une colonne de date à traiter
            if let Some(pos) = target_indices.iter().position(|&i| i == idx) {
                let hint = &payload.date_formats[pos];
                let parsed_value = parse_date_cell(field, hint);
                new_record.push(parsed_value);
            } else {
                new_record.push(field.to_string());
            }
        }
        
        if writer.write_record(&new_record).is_err() {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse { status: "error".into(), engine_used: "rust".into(), message: "Erreur d'écriture".into() }));
        }
    }

    let final_csv_bytes = match writer.into_inner() {
        Ok(bytes) => bytes,
        Err(_) => return (StatusCode::INTERNAL_SERVER_ERROR, Json(ProcessResponse { status: "error".into(), engine_used: "rust".into(), message: "Erreur tampon interne".into() })),
    };

    // 3. Sauvegarde immédiate dans le bucket "processeddata" [cite: 79]
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
            message: format!("Traitement complété par Rust avec succès pour le fichier {}", payload.file),
        }),
    )
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    let app = Router::new().route("/processDate", post(process_date_handler));

    // Utilisation du port 8001 pour coexister avec FastAPI (port 8000)
    let addr = SocketAddr::from(([127, 0, 0, 1], 8001));
    tracing::info!("Serveur Rust API démarré sur http://{}", addr);
    
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}