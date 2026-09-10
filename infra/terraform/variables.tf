variable "project_id" {
  description = "Existing GCP project ID for BloodNet."
  type        = string
}

variable "region" {
  description = "Primary BloodNet region."
  type        = string
  default     = "asia-south1"
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "production"
}

variable "database_password" {
  description = "Password for the BloodNet PostgreSQL application user."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "JWT signing secret used by BloodNet."
  type        = string
  sensitive   = true
}

variable "notification_provider_url" {
  description = "Managed SMS/WhatsApp/FCM provider gateway URL. Required for production delivery."
  type        = string
  default     = ""
  sensitive   = true
  validation {
    condition     = !var.enable_notifications || var.environment != "production" || trimspace(var.notification_provider_url) != ""
    error_message = "notification_provider_url is required when environment is production."
  }
}

variable "notification_provider_token" {
  description = "Managed notification provider bearer token. Required for production delivery."
  type        = string
  default     = ""
  sensitive   = true
  validation {
    condition     = !var.enable_notifications || var.environment != "production" || trimspace(var.notification_provider_token) != ""
    error_message = "notification_provider_token is required when environment is production."
  }
}

variable "notification_delivery_url" {
  description = "HTTPS callback URL the notification worker posts to for delivery processing. Required for production dispatch."
  type        = string
  default     = ""
  sensitive   = false
  validation {
    condition     = !var.enable_notifications || var.environment != "production" || trimspace(var.notification_delivery_url) != ""
    error_message = "notification_delivery_url is required when environment is production."
  }
}

variable "notification_default_channel" {
  description = "Provider channel used for donor notifications when donor preferences do not override it."
  type        = string
  default     = "sms"
  validation {
    condition     = contains(["sms", "whatsapp", "fcm"], lower(var.notification_default_channel))
    error_message = "notification_default_channel must be sms, whatsapp, or fcm."
  }
}

variable "notification_receipt_secret" {
  description = "HMAC secret used by the notification provider when calling the delivery receipt endpoint."
  type        = string
  default     = ""
  sensitive   = true
  validation {
    condition     = !var.enable_notifications || var.environment != "production" || trimspace(var.notification_receipt_secret) != ""
    error_message = "notification_receipt_secret is required when environment is production."
  }
}

variable "enable_notifications" {
  description = "Provision and activate the external notification delivery stack."
  type        = bool
  default     = false
}

variable "messaging_webhook_secret" {
  description = "HMAC secret shared with the managed SMS/WhatsApp inbound gateway."
  type        = string
  default     = ""
  sensitive   = true
  validation {
    condition     = !var.enable_messaging_webhook || var.environment != "production" || trimspace(var.messaging_webhook_secret) != ""
    error_message = "messaging_webhook_secret is required when the messaging webhook is enabled in production."
  }
}

variable "enable_messaging_webhook" {
  description = "Provision and activate signed SMS/WhatsApp inbound request ingestion."
  type        = bool
  default     = false
}

variable "enable_rag" {
  description = "Expose the SOP RAG capability as ready after an approved corpus has been bootstrapped."
  type        = bool
  default     = false
}

variable "artifact_repository" {
  description = "Artifact Registry Docker repository name."
  type        = string
  default     = "bloodnet"
}

variable "bigquery_dataset_id" {
  description = "BigQuery dataset ID."
  type        = string
  default     = "bloodnet"
}

variable "storage_bucket_name" {
  description = "Globally unique GCS bucket name."
  type        = string
}

variable "deploy_cloud_run" {
  description = "Deploy the optional Cloud Run service. Keep false until a real container image is available."
  type        = bool
  default     = false
}

variable "gateway_enabled" {
  description = "Deploy Cloud Armor and API Gateway in front of the BloodNet service."
  type        = bool
  default     = false
}

variable "security_policy_name" {
  description = "Cloud Armor security policy name."
  type        = string
  default     = "bloodnet-cloud-armor"
}

variable "gateway_name" {
  description = "API Gateway identifier."
  type        = string
  default     = "bloodnet-gateway"
}

variable "gateway_region" {
  description = "API Gateway deployment region. Use the closest supported region when the primary region is unavailable."
  type        = string
  default     = "asia-southeast1"
}

variable "gateway_backend_url" {
  description = "Backend URL used by the API Gateway OpenAPI config. Leave empty to use the deployed Cloud Run public endpoint."
  type        = string
  default     = ""
}

variable "gateway_api_config_id" {
  description = "API Gateway config ID. A fresh value is required for each deployment to avoid replacing the live in-use config."
  type        = string
}

variable "cloud_run_image" {
  description = "Container image for the optional Cloud Run service. The deployment script overrides this with the immutable digest of each fresh build."
  type        = string
  default     = ""
}

variable "smtp_host" {
  description = "SMTP host used for authentication and verification email delivery."
  type        = string
  default     = "smtp.gmail.com"
}

variable "smtp_port" {
  description = "SMTP submission port used for authentication and verification email delivery."
  type        = number
  default     = 587
}

variable "smtp_username_secret_id" {
  description = "Secret Manager secret ID containing the SMTP username and sender address."
  type        = string
  default     = "bloodnet-smtp-username"
}

variable "smtp_password_secret_id" {
  description = "Secret Manager secret ID containing the SMTP password."
  type        = string
  default     = "bloodnet-smtp-password"
}

variable "allowed_origins" {
  description = "Explicit HTTPS browser origins allowed by the API."
  type        = list(string)
}

variable "jwt_secret_id" {
  description = "Secret Manager secret ID containing the JWT signing secret."
  type        = string
  default     = "bloodnet-jwt-secret"
}

variable "access_policy_id" {
  description = "Access Context Manager policy ID used for the VPC Service Controls perimeter."
  type        = string
  default     = ""
}

variable "cmek_key_ring" {
  description = "KMS key ring name used for Customer Managed Encryption Keys."
  type        = string
  default     = "bloodnet"
}

variable "cmek_key_name" {
  description = "KMS crypto key name used for CMEK."
  type        = string
  default     = "bloodnet-key"
}

variable "secret_rotation_period" {
  description = "Secret Manager rotation period for application secrets."
  type        = string
  default     = "2592000s"
}

variable "dlp_template_id" {
  description = "Data Loss Prevention template ID used for de-identifying donor and patient data."
  type        = string
  default     = "bloodnet-conservative-deid"
}

variable "dlp_admin_members" {
  description = "IAM principals allowed to create and manage DLP de-identification templates. Use user:EMAIL, serviceAccount:EMAIL, or group:EMAIL."
  type        = list(string)
  default     = []
}

variable "vpc_service_perimeter_name" {
  description = "Service perimeter name for VPC Service Controls."
  type        = string
  default     = "bloodnet-prod"
}

variable "event_transport" {
  description = "Production event transport."
  type        = string
  default     = "pubsub"
  validation {
    condition     = var.event_transport == "pubsub"
    error_message = "Production Cloud Run must use Pub/Sub event transport."
  }
}

variable "pubsub_topic" {
  description = "Pub/Sub topic used by the application event adapter."
  type        = string
  default     = "bloodnet-events"
}

variable "billing_account_id" {
  description = "Optional billing account ID for a monthly budget alert."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Environment budget target."
  type        = number
  default     = 250
}

variable "monitoring_notification_channels" {
  description = "Cloud Monitoring notification channel resource names for SLO and budget alerts."
  type        = list(string)
  default     = []
}

variable "monitoring_email" {
  description = "Email address for BloodNet Cloud Monitoring alerts."
  type        = string
  default     = ""
}

variable "pubsub_push_path" {
  description = "Cloud Run path receiving validated Pub/Sub push envelopes."
  type        = string
  default     = "/pubsub/events"
}

variable "forecast_schedule" {
  description = "Cloud Scheduler cron schedule for the Blood Weather job."
  type        = string
  default     = "15 0 * * *"
}
