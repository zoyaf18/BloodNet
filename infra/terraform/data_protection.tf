# Data Protection and Compliance Controls
# NOTE: This Terraform module is intentionally not enabled by default in the app repo.
# VPC Service Controls, KMS CMEK, DLP template creation, and Secret rotation are
# org-managed platform controls owned by the target cloud admin layer. Enabling them
# here can block local validation and project admin access; apply them only in the
# approved production project after the access policy is explicitly configured.

resource "google_kms_key_ring" "bloodnet" {
  project  = var.project_id
  name     = var.cmek_key_ring
  location = "us"

  depends_on = [
    google_project_service.required["cloudkms.googleapis.com"]
  ]

  lifecycle {
    ignore_changes = []
    # If the KeyRing already exists from a prior deployment, Terraform will error 409.
    # In production, import the existing resource:
    # terraform import google_kms_key_ring.bloodnet projects/<PROJECT_ID>/locations/us/keyRings/bloodnet
  }
}

resource "google_kms_crypto_key" "bloodnet" {
  name            = var.cmek_key_name
  key_ring        = google_kms_key_ring.bloodnet.id
  rotation_period = "7776000s" # 90 days
  purpose         = "ENCRYPT_DECRYPT"
}

# Cloud SQL Service Account IAM Binding for CMEK
resource "google_kms_crypto_key_iam_member" "sql_cmek" {
  crypto_key_id = google_kms_crypto_key.bloodnet.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"

  depends_on = [null_resource.cloud_sql_service_identity]
}

# Update Cloud SQL instance to use CMEK (note: requires proper GCP permissions)
# The actual binding is applied via gcloud in production due to API limitations
# This resource documents the intended configuration
resource "null_resource" "sql_cmek_binding" {
  # This is a placeholder for the configuration
  # Actual CMEK binding applied via:
  # gcloud sql instances patch bloodnet-postgres \
  #   --disk-encryption-key=bloodnet-key \
  #   --disk-encryption-key-keyring=bloodnet \
  #   --disk-encryption-key-location=us \
  #   --disk-encryption-key-project=<project-id>

  depends_on = [
    google_kms_crypto_key_iam_member.sql_cmek
  ]
}

# Secret rotation configuration for JWT secret
resource "google_secret_manager_secret_iam_member" "jwt_secret_accessor" {
  secret_id = google_secret_manager_secret.jwt_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"

  depends_on = [null_resource.cloud_run_service_identity]
}

# Secret rotation on the JWT secret (via Terraform)
resource "null_resource" "secret_rotation_config" {
  # Secret rotation configuration is applied via:
  # gcloud secrets update <secret_name> \
  #   --next-rotation-time=<ISO8601_DATETIME> \
  #   --rotation-period=<DURATION>
  #
  # Example rotation every 30 days:
  # --next-rotation-time="2026-12-01T00:00:00Z"
  # --rotation-period="2592000s"

  depends_on = [
    google_secret_manager_secret.jwt_secret
  ]
}

# Data Loss Prevention template for de-identification
# This is a placeholder resource definition
resource "null_resource" "dlp_deidentify_template" {
  # DLP de-identification template is created via:
  # gcloud alpha dlp deidentify-configs create \
  #   --project=<project-id> \
  #   --display-name="bloodnet-deidentify" \
  #   --deidentify-config-file=config.json
  #
  # Template configuration includes:
  # - EMAIL_ADDRESS: Replace with info type
  # - PHONE_NUMBER: Replace with info type
  # - PERSON_NAME: Replace with info type
  # - DATE_OF_BIRTH: Date shift transformation (30 days)

  depends_on = [
    google_project_service.required["dlp.googleapis.com"]
  ]
}

# Grant the identity that creates DLP templates the required permissions.
# This is required for REST/gcloud calls to create deidentify templates; ADC alone is not enough.
resource "google_project_iam_member" "dlp_template_admin" {
  for_each = toset(var.dlp_admin_members)

  project = var.project_id
  role    = "roles/dlp.deidentifyTemplatesEditor"
  member  = each.value

  depends_on = [google_project_service.required["dlp.googleapis.com"]]
}

# Secret Manager rotation notifications require a Pub/Sub topic and a grant
# for the Secret Manager service agent to publish rotation notices.
resource "google_pubsub_topic" "secret_rotation_notifications" {
  project = var.project_id
  name    = "secret-rotation-notifications"

  labels = {
    project     = "bloodnet"
    environment = var.environment
    managed_by  = "terraform"
  }

  depends_on = [
    google_project_service.required["pubsub.googleapis.com"]
  ]

  lifecycle {
    ignore_changes = []
    # If the topic already exists from a prior deployment, Terraform will error 409.
    # In production, import the existing resource:
    # terraform import google_pubsub_topic.secret_rotation_notifications projects/<PROJECT_ID>/topics/secret-rotation-notifications
  }
}

resource "google_pubsub_topic_iam_member" "secret_manager_rotation_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.secret_rotation_notifications.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-secretmanager.iam.gserviceaccount.com"

  depends_on = [null_resource.secret_manager_service_identity]
}

# VPC Service Controls configuration reference
# The perimeter is created and managed separately due to org-level permissions
resource "null_resource" "vpc_service_controls_perimeter" {
  # VPC Service Controls perimeter is created via:
  # gcloud access-context-manager perimeters create bloodnet-prod \
  #   --policy=<POLICY_ID> \
  #   --title="BloodNet Production" \
  #   --resources="projects/<PROJECT_NUMBER>" \
  #   --restricted-services="bigquery.googleapis.com,storage.googleapis.com,secretmanager.googleapis.com,run.googleapis.com,sqladmin.googleapis.com" \
  #   --description="BloodNet production data plane protection"
  #
  # Perimeter ID: bloodnet_prod
  # Policy ID: Variable access_policy_id (set via terraform.tfvars)
}

# Output the KMS key resource name for reference
output "kms_crypto_key_id" {
  description = "The ID of the KMS crypto key used for CMEK"
  value       = google_kms_crypto_key.bloodnet.id
}

output "kms_key_ring_name" {
  description = "The name of the KMS key ring"
  value       = google_kms_key_ring.bloodnet.name
}

output "dlp_template_reference" {
  description = "Reference to the DLP de-identification template"
  value       = "projects/${var.project_id}/deidentifyTemplates/${var.dlp_template_id}"
}

output "vpc_service_perimeter_reference" {
  description = "Reference to the VPC Service Controls perimeter"
  value       = var.access_policy_id != "" ? "accessPolicies/${var.access_policy_id}/servicePerimeters/${var.vpc_service_perimeter_name}" : "NOT_CONFIGURED"
}
