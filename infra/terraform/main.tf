locals {
  services = toset([
    "aiplatform.googleapis.com",
    "apigateway.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "dlp.googleapis.com",
    "firestore.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com"
  ])

  labels = {
    project     = "bloodnet"
    environment = var.environment
    managed_by  = "terraform"
  }
}

moved {
  from = google_project_service.dlp
  to   = google_project_service.required["dlp.googleapis.com"]
}

resource "google_project_service" "required" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "null_resource" "cloud_sql_service_identity" {
  triggers = { project = var.project_id }

  provisioner "local-exec" {
    command = "gcloud beta services identity create --service=sqladmin.googleapis.com --project=${var.project_id} --quiet"
  }

  depends_on = [google_project_service.required["sqladmin.googleapis.com"]]
}

resource "null_resource" "cloud_run_service_identity" {
  triggers = { project = var.project_id }

  provisioner "local-exec" {
    command = "gcloud beta services identity create --service=run.googleapis.com --project=${var.project_id} --quiet"
  }

  depends_on = [google_project_service.required["run.googleapis.com"]]
}

resource "null_resource" "secret_manager_service_identity" {
  triggers = { project = var.project_id }

  provisioner "local-exec" {
    command = "gcloud beta services identity create --service=secretmanager.googleapis.com --project=${var.project_id} --quiet"
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "null_resource" "load_balancer_service_identity" {
  triggers = { project = var.project_id }

  provisioner "local-exec" {
    command = "gcloud beta services identity create --service=compute.googleapis.com --project=${var.project_id} --quiet"
  }

  depends_on = [google_project_service.required["compute.googleapis.com"]]
}

resource "google_artifact_registry_repository" "bloodnet" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository
  description   = "BloodNet container images"
  format        = "DOCKER"

  labels = local.labels

  depends_on = [
    google_project_service.required["artifactregistry.googleapis.com"]
  ]
}

data "google_compute_network" "default" {
  project = var.project_id
  name    = "default"
}

data "google_compute_subnetwork" "default" {
  project = var.project_id
  region  = var.region
  name    = "default"
}

resource "google_compute_global_address" "private_services" {
  project       = var.project_id
  name          = "bloodnet-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = data.google_compute_network.default.id
}

resource "google_service_networking_connection" "private_services" {
  network = data.google_compute_network.default.id
  service = "servicenetworking.googleapis.com"
  # Preserve ranges allocated by Cloud Build and add the SQL private-services range.
  reserved_peering_ranges = [
    "cloudbuild-service-range",
    "cloudbuild-worker-range",
    google_compute_global_address.private_services.name
  ]

  depends_on = [
    google_project_service.required["servicenetworking.googleapis.com"]
  ]
}

resource "google_sql_database_instance" "bloodnet" {
  project          = var.project_id
  name             = "bloodnet-postgres"
  region           = var.region
  database_version = "POSTGRES_16"

  deletion_protection = true

  settings {
    tier              = "db-f1-micro"
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }

    ip_configuration {
      ipv4_enabled    = true
      private_network = data.google_compute_network.default.id
    }
  }

  depends_on = [
    google_project_service.required["sqladmin.googleapis.com"],
    google_service_networking_connection.private_services
  ]
}

resource "google_sql_database" "bloodnet" {
  project  = var.project_id
  name     = "bloodnet"
  instance = google_sql_database_instance.bloodnet.name
}

resource "google_sql_user" "bloodnet" {
  project  = var.project_id
  instance = google_sql_database_instance.bloodnet.name
  name     = "bloodnet"
  password = var.database_password
}

resource "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = "bloodnet-database-url"

  replication {
    auto {}
  }

  labels = local.labels

  depends_on = [
    google_project_service.required["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id

  secret_data = "postgresql://bloodnet:${var.database_password}@/bloodnet?host=/cloudsql/${google_sql_database_instance.bloodnet.connection_name}"

  depends_on = [
    google_sql_database.bloodnet,
    google_sql_user.bloodnet
  ]
}

resource "google_firestore_database" "bloodnet" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  delete_protection_state = "DELETE_PROTECTION_DISABLED"

  depends_on = [
    google_project_service.required["firestore.googleapis.com"]
  ]
}

resource "google_bigquery_dataset" "bloodnet" {
  project    = var.project_id
  dataset_id = var.bigquery_dataset_id
  location   = var.region

  labels = local.labels

  default_table_expiration_ms = 7776000000 # 90 days for demo data

  delete_contents_on_destroy = false

  depends_on = [
    google_project_service.required["bigquery.googleapis.com"]
  ]
}

resource "google_bigquery_table" "demand_history" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bloodnet.dataset_id
  table_id   = "demand_history"

  schema = jsonencode([
    { name = "shortage_units", type = "INT64", mode = "NULLABLE" },
    { name = "fulfilled_units", type = "INT64", mode = "NULLABLE" },
    { name = "requested_units", type = "INT64", mode = "NULLABLE" },
    { name = "hospital_id", type = "STRING", mode = "NULLABLE" },
    { name = "component", type = "STRING", mode = "NULLABLE" },
    { name = "blood_group", type = "STRING", mode = "NULLABLE" },
    { name = "region", type = "STRING", mode = "NULLABLE" },
    { name = "demand_date", type = "DATE", mode = "NULLABLE" },
  ])

  labels = local.labels

  depends_on = [google_bigquery_dataset.bloodnet]
}

resource "google_bigquery_table" "forecast_results" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bloodnet.dataset_id
  table_id   = "forecast_results"

  schema = jsonencode([
    { name = "forecast_run_id", type = "STRING", mode = "REQUIRED" },
    { name = "forecast_date", type = "DATE", mode = "REQUIRED" },
    { name = "target_date", type = "DATE", mode = "REQUIRED" },
    { name = "region", type = "STRING", mode = "REQUIRED" },
    { name = "blood_group", type = "STRING", mode = "REQUIRED" },
    { name = "component", type = "STRING", mode = "REQUIRED" },
    { name = "predicted_demand", type = "NUMERIC", mode = "REQUIRED" },
    { name = "lower_bound", type = "NUMERIC", mode = "REQUIRED" },
    { name = "upper_bound", type = "NUMERIC", mode = "REQUIRED" },
    { name = "projected_supply", type = "NUMERIC", mode = "REQUIRED" },
    { name = "shortage_probability", type = "FLOAT64", mode = "REQUIRED" },
    { name = "confidence_level", type = "FLOAT64", mode = "REQUIRED" },
    { name = "model_version", type = "STRING", mode = "REQUIRED" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])

  labels = local.labels

  depends_on = [google_bigquery_dataset.bloodnet]
}

resource "google_bigquery_table" "supply_projection" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bloodnet.dataset_id
  table_id   = "supply_projection"

  schema = jsonencode([
    { name = "target_date", type = "DATE", mode = "REQUIRED" },
    { name = "region", type = "STRING", mode = "REQUIRED" },
    { name = "blood_group", type = "STRING", mode = "REQUIRED" },
    { name = "component", type = "STRING", mode = "REQUIRED" },
    { name = "projected_supply", type = "NUMERIC", mode = "REQUIRED" },
  ])

  labels = local.labels

  depends_on = [google_bigquery_dataset.bloodnet]
}

resource "google_bigquery_table" "weather_observations" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bloodnet.dataset_id
  table_id   = "weather_observations"

  schema = jsonencode([
    { name = "observation_date", type = "DATE", mode = "REQUIRED" },
    { name = "region", type = "STRING", mode = "REQUIRED" },
    { name = "latitude", type = "FLOAT64", mode = "REQUIRED" },
    { name = "longitude", type = "FLOAT64", mode = "REQUIRED" },
    { name = "data_kind", type = "STRING", mode = "REQUIRED" },
    { name = "temperature_c", type = "FLOAT64", mode = "REQUIRED" },
    { name = "precipitation_mm", type = "FLOAT64", mode = "REQUIRED" },
    { name = "rain_mm", type = "FLOAT64", mode = "REQUIRED" },
    { name = "weather_code", type = "INT64", mode = "REQUIRED" },
    { name = "weather_flag", type = "BOOL", mode = "REQUIRED" },
    { name = "ingested_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])

  time_partitioning {
    type  = "DAY"
    field = "observation_date"
  }

  clustering = ["region", "data_kind"]
  labels     = local.labels

  depends_on = [google_bigquery_dataset.bloodnet]
}

resource "google_bigquery_table" "events" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bloodnet.dataset_id
  table_id   = "events"

  schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "event_type", type = "STRING", mode = "REQUIRED" },
    { name = "schema_version", type = "STRING", mode = "REQUIRED" },
    { name = "request_id", type = "STRING", mode = "REQUIRED" },
    { name = "case_id", type = "STRING", mode = "NULLABLE" },
    { name = "correlation_id", type = "STRING", mode = "REQUIRED" },
    { name = "timestamp", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "payload", type = "JSON", mode = "REQUIRED" },
    { name = "source", type = "STRING", mode = "REQUIRED" },
  ])

  labels = local.labels

  depends_on = [google_bigquery_dataset.bloodnet]
}

resource "google_storage_bucket" "bloodnet" {
  project                     = var.project_id
  name                        = var.storage_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  labels = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }

    action {
      type = "Delete"
    }
  }

  depends_on = [
    google_project_service.required["storage.googleapis.com"]
  ]
}

resource "google_pubsub_topic" "topics" {
  for_each = toset([
    "bloodnet-events",
    "bloodnet-request-events",
    "bloodnet-case-events",
    "bloodnet-recommendations",
    "bloodnet-audit-events"
  ])

  project = var.project_id
  name    = each.value

  labels = local.labels

  depends_on = [
    google_project_service.required["pubsub.googleapis.com"]
  ]
}

resource "google_pubsub_topic" "dead_letter" {
  for_each = var.deploy_cloud_run ? toset([
    "bloodnet-dead-letter-events",
  ]) : toset([])

  project = var.project_id
  name    = each.value
  labels  = local.labels

  depends_on = [google_project_service.required["pubsub.googleapis.com"]]
}

locals {
  pubsub_subscriptions = {
    request_events  = "bloodnet-request-events"
    case_events     = "bloodnet-case-events"
    recommendations = "bloodnet-recommendations"
    audit_events    = "bloodnet-audit-events"
    all_events      = "bloodnet-events"
  }
}

resource "google_pubsub_subscription" "push" {
  for_each = var.deploy_cloud_run ? local.pubsub_subscriptions : {}

  project = var.project_id
  name    = "${each.key}-${var.environment}"
  topic   = google_pubsub_topic.topics[each.value].id

  ack_deadline_seconds = 30

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter["bloodnet-dead-letter-events"].id
    max_delivery_attempts = 5
  }

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.api[0].uri}${var.pubsub_push_path}"
    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = google_cloud_run_v2_service.api[0].uri
    }
  }

  depends_on = [
    google_cloud_run_v2_service.api,
    google_project_iam_member.pubsub_push_token_creator,
  ]
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  for_each = var.deploy_cloud_run ? local.pubsub_subscriptions : {}

  project = var.project_id
  topic   = google_pubsub_topic.dead_letter["bloodnet-dead-letter-events"].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each = var.deploy_cloud_run ? local.pubsub_subscriptions : {}

  project      = var.project_id
  subscription = google_pubsub_subscription.push[each.key].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "bloodnet-runtime"
  display_name = "BloodNet runtime service account"
}

resource "google_service_account" "pubsub_push" {
  project      = var.project_id
  account_id   = "bloodnet-pubsub-push"
  display_name = "BloodNet Pub/Sub push identity"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset([
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/pubsub.publisher",
    "roles/pubsub.subscriber",
    "roles/secretmanager.secretAccessor",
    "roles/storage.objectAdmin",
    "roles/aiplatform.user"
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "pubsub_push_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_service_account_iam_member" "pubsub_push_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_secret_manager_secret" "jwt_secret" {
  project   = var.project_id
  secret_id = var.jwt_secret_id

  replication {
    auto {}
  }

  labels = local.labels

  depends_on = [
    google_project_service.required["secretmanager.googleapis.com"]
  ]
}

resource "google_secret_manager_secret_version" "jwt_secret" {
  secret      = google_secret_manager_secret.jwt_secret.id
  secret_data = var.jwt_secret

  lifecycle {
    ignore_changes = [secret_data]
  }
}

data "google_secret_manager_secret" "smtp_username" {
  project   = var.project_id
  secret_id = var.smtp_username_secret_id
}

data "google_secret_manager_secret" "smtp_password" {
  project   = var.project_id
  secret_id = var.smtp_password_secret_id
}

resource "google_secret_manager_secret" "notification_provider_url" {
  count     = var.enable_notifications ? 1 : 0
  project   = var.project_id
  secret_id = "bloodnet-notification-provider-url"
  replication {
    auto {}
  }
  labels     = local.labels
  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "notification_provider_url" {
  count       = var.enable_notifications ? 1 : 0
  secret      = google_secret_manager_secret.notification_provider_url[0].id
  secret_data = var.notification_provider_url
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret" "notification_provider_token" {
  count     = var.enable_notifications ? 1 : 0
  project   = var.project_id
  secret_id = "bloodnet-notification-provider-token"
  replication {
    auto {}
  }
  labels     = local.labels
  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "notification_provider_token" {
  count       = var.enable_notifications ? 1 : 0
  secret      = google_secret_manager_secret.notification_provider_token[0].id
  secret_data = var.notification_provider_token
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret" "notification_receipt_secret" {
  count     = var.enable_notifications ? 1 : 0
  project   = var.project_id
  secret_id = "bloodnet-notification-receipt-secret"
  replication {
    auto {}
  }
  labels     = local.labels
  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "notification_receipt_secret" {
  count       = var.enable_notifications ? 1 : 0
  secret      = google_secret_manager_secret.notification_receipt_secret[0].id
  secret_data = var.notification_receipt_secret
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_secret_manager_secret" "messaging_webhook_secret" {
  count     = var.enable_messaging_webhook ? 1 : 0
  project   = var.project_id
  secret_id = "bloodnet-messaging-webhook-secret"

  replication {
    auto {}
  }
  labels     = local.labels
  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret_version" "messaging_webhook_secret" {
  count       = var.enable_messaging_webhook ? 1 : 0
  secret      = google_secret_manager_secret.messaging_webhook_secret[0].id
  secret_data = var.messaging_webhook_secret
  lifecycle { ignore_changes = [secret_data] }
}

resource "google_cloud_run_v2_service" "api" {
  count = var.deploy_cloud_run ? 1 : 0

  project  = var.project_id
  name     = "bloodnet-api"
  location = var.region
  client   = "terraform"

  deletion_protection = false
  # Only explicitly granted service identities (API Gateway and Pub/Sub) may
  # invoke Cloud Run. User authentication is enforced by the application.
  invoker_iam_disabled = false
  ingress              = var.gateway_enabled ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.runtime.email
    timeout         = "300s"

    volumes {
      name = "cloudsql"

      cloud_sql_instance {
        instances = [
          google_sql_database_instance.bloodnet.connection_name
        ]
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"

      network_interfaces {
        network    = data.google_compute_network.default.name
        subnetwork = data.google_compute_subnetwork.default.name
      }
    }

    containers {
      image = var.cloud_run_image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "BLOODNET_ENV"
        value = var.environment
      }

      env {
        name  = "BLOODNET_ENABLE_NOTIFICATIONS"
        value = tostring(var.enable_notifications)
      }

      env {
        name  = "BLOODNET_RAG_ENABLED"
        value = tostring(var.enable_rag)
      }

      dynamic "env" {
        for_each = var.enable_messaging_webhook ? [1] : []
        content {
          name = "BLOODNET_MESSAGING_WEBHOOK_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.messaging_webhook_secret[0].id
              version = google_secret_manager_secret_version.messaging_webhook_secret[0].version
            }
          }
        }
      }

      env {
        name  = "BLOODNET_IAP_AUDIENCE"
        value = var.project_id
      }

      env {
        name  = "BLOODNET_IDENTITY_PLATFORM_AUDIENCE"
        value = var.project_id
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "GCP_REGION"
        value = var.region
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }

      env {
        name  = "BLOODNET_GEMINI_USE_VERTEX_AI"
        value = "true"
      }

      env {
        name  = "BLOODNET_GEMINI_MODEL"
        value = "gemini-2.5-flash"
      }

      env {
        name  = "BLOODNET_BQ_DATASET"
        value = google_bigquery_dataset.bloodnet.dataset_id
      }

      env {
        name  = "BLOODNET_BQ_EVENTS_ENABLED"
        value = "true"
      }

      env {
        name = "BLOODNET_DATABASE_URL"

        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.id
            version = google_secret_manager_secret_version.database_url.version
          }
        }
      }

      env {
        name  = "BLOODNET_EVENT_TRANSPORT"
        value = var.event_transport
      }

      env {
        name  = "BLOODNET_AUTH_MODE"
        value = "production"
      }

      env {
        name  = "BLOODNET_PUBSUB_TOPIC"
        value = var.pubsub_topic
      }

      env {
        name  = "BLOODNET_INTAKE_PROVIDER"
        value = "gemini"
      }

      env {
        name  = "BLOODNET_ALLOWED_ORIGINS"
        value = join(",", var.allowed_origins)
      }

      env {
        name  = "BLOODNET_FRONTEND_ORIGIN"
        value = var.allowed_origins[0]
      }

      env {
        name  = "BLOODNET_SMTP_HOST"
        value = var.smtp_host
      }

      env {
        name  = "BLOODNET_SMTP_PORT"
        value = tostring(var.smtp_port)
      }

      env {
        name = "BLOODNET_SMTP_USERNAME"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.smtp_username.id
            version = "latest"
          }
        }
      }

      env {
        name = "BLOODNET_SMTP_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.smtp_password.id
            version = "latest"
          }
        }
      }

      env {
        name = "BLOODNET_EMAIL_FROM"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.smtp_username.id
            version = "latest"
          }
        }
      }

      env {
        name = "BLOODNET_JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret.id
            version = "latest"
          }
        }
      }

      dynamic "env" {
        for_each = var.enable_notifications ? [1] : []
        content {
          name = "BLOODNET_NOTIFICATION_PROVIDER_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.notification_provider_url[0].id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = var.enable_notifications ? [1] : []
        content {
          name = "BLOODNET_NOTIFICATION_PROVIDER_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.notification_provider_token[0].id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = var.enable_notifications ? [1] : []
        content {
          name  = "BLOODNET_CLOUD_TASKS_QUEUE"
          value = google_cloud_tasks_queue.notification_delivery[0].name
        }
      }

      dynamic "env" {
        for_each = var.enable_notifications ? [1] : []
        content {
          name  = "BLOODNET_NOTIFICATION_DELIVERY_URL"
          value = var.notification_delivery_url
        }
      }

      env {
        name  = "BLOODNET_RUNTIME_SERVICE_ACCOUNT"
        value = google_service_account.runtime.email
      }
      env {
        name  = "BLOODNET_NOTIFICATION_DEFAULT_CHANNEL"
        value = var.notification_default_channel
      }
      dynamic "env" {
        for_each = var.enable_notifications ? [1] : []
        content {
          name = "BLOODNET_NOTIFICATION_RECEIPT_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.notification_receipt_secret[0].id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_project_service.required["sqladmin.googleapis.com"],
    google_artifact_registry_repository.bloodnet,
    google_project_iam_member.runtime_roles,
    google_project_iam_member.runtime_cloudsql_client,
    google_project_iam_member.runtime_roles["roles/secretmanager.secretAccessor"],
    google_secret_manager_secret_version.database_url,
    google_cloud_tasks_queue.notification_delivery
  ]

  lifecycle {
    precondition {
      condition     = var.cloud_run_image != ""
      error_message = "cloud_run_image must be set when deploy_cloud_run=true."
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_invoker" {
  count = var.deploy_cloud_run ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.api[0].location
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push.email}"
}

resource "google_cloud_run_v2_service_iam_member" "api_gateway_invoker" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.api[0].location
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-apigateway.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_service_iam_member" "serverless_neg_invoker" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.api[0].location
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

resource "google_service_account" "forecast_scheduler" {
  count        = var.deploy_cloud_run ? 1 : 0
  project      = var.project_id
  account_id   = "bloodnet-forecast-scheduler"
  display_name = "BloodNet forecast scheduler"
}

resource "google_cloud_run_v2_job" "forecast" {
  count    = var.deploy_cloud_run ? 1 : 0
  project  = var.project_id
  name     = "bloodnet-forecast"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "1800s"

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.bloodnet.connection_name]
        }
      }

      containers {
        image   = var.cloud_run_image
        command = ["python"]
        args    = ["ml/demand_forecast/forecast_job.py"]

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        env {
          name  = "BLOODNET_ENV"
          value = var.environment
        }
        env {
          name  = "BLOODNET_NOTIFICATION_DEFAULT_CHANNEL"
          value = var.notification_default_channel
        }
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "BLOODNET_BIGQUERY_DATASET"
          value = google_bigquery_dataset.bloodnet.dataset_id
        }
        env {
          name  = "PYTHONPATH"
          value = "/app"
        }
        env {
          name = "BLOODNET_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_project_service.required["bigquery.googleapis.com"],
    google_project_iam_member.runtime_roles,
    google_project_iam_member.runtime_cloudsql_client,
    google_secret_manager_secret_version.database_url,
  ]
}

resource "google_project_iam_member" "forecast_scheduler_run_developer" {
  count   = var.deploy_cloud_run ? 1 : 0
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.forecast_scheduler[0].email}"
}

resource "google_service_account_iam_member" "forecast_scheduler_runtime_user" {
  count              = var.deploy_cloud_run ? 1 : 0
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.forecast_scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "forecast" {
  count       = var.deploy_cloud_run ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "bloodnet-forecast-daily"
  description = "Refresh Open-Meteo weather features and Blood Weather forecasts"
  schedule    = var.forecast_schedule
  time_zone   = "Asia/Kolkata"

  retry_config {
    retry_count = 3
  }

  http_target {
    uri         = "https://run.googleapis.com/apis/run.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.forecast[0].name}:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.forecast_scheduler[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_cloud_run_v2_job.forecast,
    google_project_iam_member.forecast_scheduler_run_developer,
    google_service_account_iam_member.forecast_scheduler_runtime_user,
  ]
}

resource "google_cloud_tasks_queue" "notification_delivery" {
  count    = var.deploy_cloud_run && var.enable_notifications ? 1 : 0
  project  = var.project_id
  name     = "bloodnet-notification-delivery"
  location = var.region

  rate_limits {
    max_dispatches_per_second = 20
    max_concurrent_dispatches = 50
  }

  retry_config {
    max_attempts       = 10
    max_retry_duration = "3600s"
    max_backoff        = "300s"
  }

  depends_on = [google_project_service.required["cloudtasks.googleapis.com"]]
}

resource "google_cloud_run_v2_service_iam_member" "notification_worker_invoker" {
  count    = var.deploy_cloud_run && var.enable_notifications ? 1 : 0
  project  = var.project_id
  location = google_cloud_run_v2_service.api[0].location
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_job" "notification_worker" {
  count    = var.deploy_cloud_run && var.enable_notifications ? 1 : 0
  project  = var.project_id
  name     = "bloodnet-notification-worker"
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      timeout         = "900s"
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.bloodnet.connection_name]
        }
      }
      containers {
        image   = var.cloud_run_image
        command = ["python"]
        args    = ["services/notify-svc/worker.py"]
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
        env {
          name  = "BLOODNET_ENV"
          value = var.environment
        }
        env {
          name  = "PYTHONPATH"
          value = "/app/services/notify-svc:/app"
        }
        env {
          name = "BLOODNET_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_cloud_tasks_queue.notification_delivery,
    google_project_iam_member.runtime_roles,
    google_project_iam_member.runtime_cloudsql_client,
    google_secret_manager_secret_version.database_url,
  ]
}

resource "google_cloud_scheduler_job" "notification_recovery" {
  count       = var.deploy_cloud_run && var.enable_notifications ? 1 : 0
  project     = var.project_id
  region      = var.region
  name        = "bloodnet-notification-recovery"
  description = "Recover leased notifications and deliver the durable outbox"
  schedule    = "*/1 * * * *"
  time_zone   = "Asia/Kolkata"

  http_target {
    uri         = "https://run.googleapis.com/apis/run.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.notification_worker[0].name}:run"
    http_method = "POST"
    oauth_token {
      service_account_email = google_service_account.forecast_scheduler[0].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job.notification_worker]
}

resource "google_billing_budget" "bloodnet" {
  count = var.billing_account_id != "" ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "BloodNet ${var.environment} monthly budget"

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.8
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    monitoring_notification_channels = var.monitoring_notification_channels
  }
}
