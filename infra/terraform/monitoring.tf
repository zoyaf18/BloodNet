resource "google_monitoring_notification_channel" "email" {
  count        = var.monitoring_email != "" ? 1 : 0
  display_name = "BloodNet ${var.environment} email alerts"
  type         = "email"
  project      = var.project_id

  labels = {
    email_address = var.monitoring_email
  }
}

locals {
  bloodnet_notification_channels = concat(
    var.monitoring_notification_channels,
    var.monitoring_email != "" ? [google_monitoring_notification_channel.email[0].name] : [],
  )
}

resource "google_logging_metric" "notification_delivery_failures" {
  count  = var.deploy_cloud_run ? 1 : 0
  name   = "bloodnet-notification-delivery-failures"
  filter = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"bloodnet-api\" AND textPayload:\"notification_delivery_failed\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "notification_delivery_failures" {
  count        = var.deploy_cloud_run ? 1 : 0
  display_name = "BloodNet ${var.environment} notification delivery failures"
  combiner     = "OR"

  conditions {
    display_name = "Notification provider failures"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/bloodnet-notification-delivery-failures\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.bloodnet_notification_channels
  enabled               = true
  documentation {
    content   = "Notification provider delivery failed. Inspect the durable outbox retry state and provider response logs."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_request_errors" {
  count        = var.deploy_cloud_run ? 1 : 0
  display_name = "BloodNet ${var.environment} request errors"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx responses"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"bloodnet-api\" AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = local.bloodnet_notification_channels
  enabled               = true
  documentation {
    content   = "BloodNet API is returning 5xx responses. Check Cloud Run logs and dependency health."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_latency" {
  count        = var.deploy_cloud_run ? 1 : 0
  display_name = "BloodNet ${var.environment} request latency"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run request latency above target"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"bloodnet-api\" AND metric.type=\"run.googleapis.com/request_latencies\""
      comparison      = "COMPARISON_GT"
      threshold_value = 6000
      duration        = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = local.bloodnet_notification_channels
  enabled               = true
  documentation {
    content   = "BloodNet API p95 latency exceeded the intake budget."
    mime_type = "text/markdown"
  }
}
