output "artifact_registry_repository" {
  value = google_artifact_registry_repository.bloodnet.name
}

output "artifact_registry_docker_path" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository}"
}

output "bigquery_dataset" {
  value = google_bigquery_dataset.bloodnet.dataset_id
}

output "storage_bucket" {
  value = google_storage_bucket.bloodnet.name
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "pubsub_push_service_account" {
  value = google_service_account.pubsub_push.email
}

output "pubsub_topics" {
  value = [
    for topic in google_pubsub_topic.topics : topic.name
  ]
}

output "cloud_run_url" {
  value = var.deploy_cloud_run ? google_cloud_run_v2_service.api[0].uri : null
}
