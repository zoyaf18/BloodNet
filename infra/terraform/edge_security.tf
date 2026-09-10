resource "google_compute_security_policy" "bloodnet_edge" {
  count = var.gateway_enabled ? 1 : 0

  project = var.project_id
  name    = var.security_policy_name

  depends_on = [
    google_project_service.required["compute.googleapis.com"],
  ]

  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "default allow rule"
  }

  rule {
    action   = "deny(403)"
    priority = "1000"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('xss-stable') || evaluatePreconfiguredExpr('sqli-stable')"
      }
    }
    description = "Block common WAF attack payloads"
  }

  rule {
    action   = "rate_based_ban"
    priority = "2000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
      ban_duration_sec = 300
      rate_limit_threshold {
        count        = 200
        interval_sec = 60
      }
    }
    description = "Throttle abusive request bursts"
  }
}

resource "google_compute_global_address" "bloodnet_edge" {
  count = var.gateway_enabled ? 1 : 0

  project = var.project_id
  name    = "bloodnet-edge-ip"
}

resource "google_compute_region_network_endpoint_group" "bloodnet_run" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project = var.project_id
  name    = "bloodnet-run-neg"
  region  = var.region

  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.api[0].name
  }

  depends_on = [
    google_cloud_run_v2_service.api,
    google_project_service.required["compute.googleapis.com"],
  ]
}

resource "google_compute_backend_service" "bloodnet_edge" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project = var.project_id
  name    = "bloodnet-edge-backend"

  protocol              = "HTTP"
  port_name             = "http"
  load_balancing_scheme = "EXTERNAL"
  security_policy       = google_compute_security_policy.bloodnet_edge[0].id

  backend {
    group = google_compute_region_network_endpoint_group.bloodnet_run[0].id
  }

  depends_on = [
    google_compute_region_network_endpoint_group.bloodnet_run,
    google_compute_security_policy.bloodnet_edge,
  ]
}

resource "google_compute_url_map" "bloodnet_edge" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project         = var.project_id
  name            = "bloodnet-edge-url-map"
  default_service = google_compute_backend_service.bloodnet_edge[0].id
}

resource "google_compute_target_http_proxy" "bloodnet_edge" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project = var.project_id
  name    = "bloodnet-edge-http-proxy"
  url_map = google_compute_url_map.bloodnet_edge[0].id
}

resource "google_compute_global_forwarding_rule" "bloodnet_edge" {
  count = var.gateway_enabled && var.deploy_cloud_run ? 1 : 0

  project               = var.project_id
  name                  = "bloodnet-edge-lb"
  target                = google_compute_target_http_proxy.bloodnet_edge[0].id
  port_range            = "80"
  ip_address            = google_compute_global_address.bloodnet_edge[0].address
  load_balancing_scheme = "EXTERNAL"
}

locals {
  gateway_backend_address = var.gateway_backend_url != "" ? var.gateway_backend_url : try(google_cloud_run_v2_service.api[0].uri, "https://bloodnet-api.internal")
  edge_lb_address         = var.gateway_enabled && var.deploy_cloud_run ? google_compute_global_address.bloodnet_edge[0].address : null
}

resource "google_api_gateway_api" "bloodnet" {
  provider = google-beta
  count    = var.gateway_enabled ? 1 : 0

  project    = var.project_id
  api_id     = var.gateway_name
  depends_on = [google_project_service.required["apigateway.googleapis.com"]]
}

resource "google_api_gateway_api_config" "bloodnet" {
  provider = google-beta
  count    = var.gateway_enabled ? 1 : 0

  project = var.project_id
  api     = google_api_gateway_api.bloodnet[0].api_id

  # Keep this ID stable. GCP API Gateway manages the live config pointer on the gateway itself,
  # and replacing an in-use config can hit a 409 while the gateway still points at it.
  # This is a managed-service lifecycle constraint, not an application bug.
  api_config_id = var.gateway_api_config_id
  depends_on    = [google_api_gateway_api.bloodnet, google_cloud_run_v2_service.api]

  lifecycle {
    create_before_destroy = true
  }

  openapi_documents {
    document {
      path = "openapi-spec.yaml"
      contents = base64encode(<<-EOT
swagger: "2.0"
info:
  title: BloodNet API
  version: "1.0.0"
securityDefinitions:
  api_key:
    type: apiKey
    name: key
    in: query
  firebase_auth:
    type: oauth2
    flow: implicit
    authorizationUrl: ""
    x-google-issuer: https://securetoken.google.com/${var.project_id}
    x-google-jwks_uri: https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com
    x-google-audiences: ${var.project_id}
x-google-backend:
  address: ${local.gateway_backend_address}
  path_translation: APPEND_PATH_TO_ADDRESS
  deadline: 300.0
  disable_auth: false
paths:
  /health:
    options:
      operationId: cors_health
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: health
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/public/dashboard:
    options:
      operationId: cors_match_svc_api_v1_public_dashboard
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: public_regional_dashboard
      parameters:
        - name: region
          in: query
          required: false
          type: string
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Privacy-safe regional dashboard aggregates
  /match-svc/api/v1/public/demo-scenarios:
    options:
      operationId: cors_match_svc_api_v1_public_demo_scenarios
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: public_demo_scenarios
      parameters:
        - name: region
          in: query
          required: false
          type: string
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Read-only visual demonstration scenarios
  /match-svc/api/v1/public/demo-scenarios/{scenario_id}/run:
    options:
      operationId: cors_match_svc_api_v1_public_demo_scenarios_scenario_id_run
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: scenario_id
        in: path
        required: true
        type: string
    post:
      operationId: run_public_demo_scenario
      parameters:
        - name: region
          in: query
          required: false
          type: string
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Visual demonstration result
        "404":
          description: Scenario not found
  /api/v1/capabilities:
    options:
      operationId: cors_capabilities_get
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: capabilities_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Optional capability readiness
  /api/v1/requests/ingest:
    options:
      operationId: cors_api_v1_requests_ingest
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: inbound_request_ingest
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Request accepted and dispatched
  /api/v1/clinical/requests/ingest:
    options:
      operationId: cors_api_v1_clinical_requests_ingest
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: clinical_request_ingest
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Clinical request accepted and dispatched
  /api/v1/clinical/requests/preview:
    options:
      operationId: cors_api_v1_clinical_requests_preview
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: clinical_request_preview
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Clinical request preview returned for human review
  /api/v1/inbound/messaging:
    options:
      operationId: cors_api_v1_inbound_messaging
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: inbound_messaging_request
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Provider-authenticated SMS or WhatsApp request accepted
  /api/v1/inbound/messaging/events:
    options:
      operationId: cors_inbound_messaging_events
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: inbound_messaging_events
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Role-scoped messaging ingress activity
  /match-svc/api/v1/auth/login:
    options:
      operationId: cors_auth_login
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_login
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/auth/signup:
    options:
      operationId: cors_auth_signup
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_signup
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "202":
          description: accepted
  /match-svc/api/v1/organizations/onboard:
    options:
      operationId: cors_organization_onboard
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: public_organization_onboard
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "202":
          description: Hospital or blood-bank registration accepted
  /match-svc/api/v1/auth/region-preference:
    options:
      operationId: cors_auth_region_preference
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: auth_region_preference_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Saved service region
    put:
      operationId: auth_region_preference_save
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Service region saved
  /match-svc/api/v1/auth/verify-email:
    options:
      operationId: cors_auth_verify_email
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_verify_email
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/invitations:
    options:
      operationId: cors_invitations_create
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_invitations_create
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "201":
          description: Invitation created
  /match-svc/api/v1/invitations/{token}/accept:
    parameters:
      - name: token
        in: path
        required: true
        type: string
    options:
      operationId: cors_invitation_accept
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_invitation_accept
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Invitation accepted
  /match-svc/api/v1/auth/password-reset:
    options:
      operationId: cors_match_svc_api_v1_auth_password_reset
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_password_reset
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "202":
          description: accepted
  /match-svc/api/v1/auth/password-reset-confirm:
    options:
      operationId: cors_match_svc_api_v1_auth_password_reset_confirm
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_password_reset_confirm
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/auth/role-requests:
    options:
      operationId: cors_auth_role_requests
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: list_auth_role_requests
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
    post:
      operationId: request_auth_role_access
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "202":
          description: accepted
  /match-svc/api/v1/auth/role-requests/{request_id}/{decision}:
    options:
      operationId: cors_decide_auth_role_request
      security: []
      parameters:
        - name: request_id
          in: path
          required: true
          type: string
        - name: decision
          in: path
          required: true
          type: string
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: decide_auth_role_request
      security:
        - firebase_auth: []
        - api_key: []
      parameters:
        - name: request_id
          in: path
          required: true
          type: string
        - name: decision
          in: path
          required: true
          type: string
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/auth/mfa/verify:
    options:
      operationId: cors_match_svc_api_v1_auth_mfa_verify
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_auth_mfa_verify
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/auth/location:
    options:
      operationId: cors_auth_location
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    put:
      operationId: update_auth_location
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Location updated
  /match-svc/api/v1/auth/me:
    options:
      operationId: cors_auth_me
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: match_auth_me
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/me:
    options:
      operationId: cors_me
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: match_me
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases:
    options:
      operationId: cors_cases
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: match_cases_list
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/match:
    options:
      operationId: cors_match
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_create
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    get:
      operationId: match_case_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/stream:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_stream
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    get:
      operationId: match_case_stream
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/recommendations:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_recommendations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    get:
      operationId: match_case_recommendations
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/approve:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_reservations_rec_id_approve
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
      - name: rec_id
        in: path
        required: true
        type: string
    post:
      operationId: match_reservation_approve
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/reject:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_reservations_rec_id_reject
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
      - name: rec_id
        in: path
        required: true
        type: string
    post:
      operationId: match_reservation_reject
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/fulfill:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_fulfill
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    post:
      operationId: match_case_fulfill
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/cancel:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_cancel
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    post:
      operationId: match_case_cancel
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/cases/{case_id}/escalate:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_escalate
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    post:
      operationId: match_case_escalate
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/inventory:
    options:
      operationId: cors_match_svc_api_v1_inventory
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: inventory_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/inventory/{unit_id}:
    options:
      operationId: cors_match_svc_api_v1_inventory_unit_id
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: unit_id
        in: path
        required: true
        type: string
    get:
      operationId: inventory_unit_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/inventory/transfers:
    options:
      operationId: cors_match_svc_api_v1_inventory_transfers
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: inventory_transfer
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/integrations/blood-bank/inventory:
    options:
      operationId: cors_match_svc_api_v1_integrations_blood_bank_inventory
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: blood_bank_inventory_sync
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Inventory synchronization accepted
  /match-svc/api/v1/inventory/transfers/{transfer_id}/receive:
    options:
      operationId: cors_match_svc_api_v1_inventory_transfers_transfer_id_receive
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: transfer_id
        in: path
        required: true
        type: string
    post:
      operationId: inventory_transfer_receive
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/inventory/reservations/{reservation_id}/consume:
    options:
      operationId: cors_match_svc_api_v1_inventory_reservations_reservation_id_consume
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: reservation_id
        in: path
        required: true
        type: string
    post:
      operationId: inventory_reservation_consume
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/hospital/inventory:
    options:
      operationId: cors_match_svc_api_v1_hospital_inventory
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: hospital_inventory_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/hospital/context:
    options:
      operationId: cors_hospital_context
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: hospital_context_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/network/health:
    options:
      operationId: cors_network_health
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: network_health_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/reservations/pending:
    options:
      operationId: cors_pending_reservations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: pending_reservations_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/recommendations:
    options:
      operationId: cors_recommendations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: recommendations_list
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/regional/forecast:
    options:
      operationId: cors_regional_forecast
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: regional_forecast
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/regional/swarms:
    options:
      operationId: cors_regional_swarms
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: regional_swarms
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/notifications:
    options:
      operationId: cors_notifications
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: notifications_list
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/notifications/delivery-operations:
    options:
      operationId: cors_notification_delivery_operations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: notification_delivery_operations
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Role-scoped notification delivery state
  /match-svc/api/v1/notifications/provider-receipt:
    options:
      operationId: cors_match_svc_api_v1_notifications_provider_receipt
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: notification_provider_receipt
      security:
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: HMAC-authenticated provider delivery receipt
  /match-svc/api/v1/auth/donor-profile:
    options:
      operationId: cors_donor_profile
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: donor_profile_get
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
    put:
      operationId: donor_profile_update
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/audit:
    options:
      operationId: cors_audit
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: audit_list
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/audit/search:
    options:
      operationId: cors_audit_search
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: audit_search
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/audit/activity:
    options:
      operationId: cors_audit_activity
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: audit_activity
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/me/session:
    options:
      operationId: cors_session
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: session_get
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/privacy/policies:
    options:
      operationId: cors_privacy_policies
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: privacy_policies
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK
  /match-svc/api/v1/recommendations/{rec_id}/approve:
    options:
      operationId: cors_match_svc_api_v1_recommendations_rec_id_approve
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: rec_id
        in: path
        required: true
        type: string
    post:
      operationId: recommendation_approve
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/recommendations/{rec_id}/reject:
    options:
      operationId: cors_match_svc_api_v1_recommendations_rec_id_reject
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: rec_id
        in: path
        required: true
        type: string
    post:
      operationId: recommendation_reject
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /swarm-svc/api/v1/opportunities:
    options:
      operationId: cors_swarm_opportunities
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: swarm_opportunities
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /swarm-svc/api/v1/outreach/{outreach_id}/response:
    options:
      operationId: cors_swarm_svc_api_v1_outreach_outreach_id_response
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: outreach_id
        in: path
        required: true
        type: string
    post:
      operationId: swarm_outreach_response
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /agent-svc/api/v1/investigations/gemini:
    options:
      operationId: cors_agent_investigations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: agent_investigations_gemini
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
        deadline: 60.0
      responses:
        "200":
          description: OK
  /agent-svc/api/v1/sops/status:
    options:
      operationId: cors_agent_sops_status
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: agent_sops_status
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Approved SOP corpus readiness
  /agent-svc/api/v1/sops/search:
    options:
      operationId: cors_agent_sops_search
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: agent_sops_search
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
        deadline: 60.0
      responses:
        "200":
          description: Citation-bearing SOP evidence
  /agent-svc/api/v1/recommendations/gemini:
    options:
      operationId: agent_recommendations_gemini_cors
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight
    post:
      operationId: agent_recommendations_gemini
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /graph-svc/api/v1/graph/analysis:
    options:
      operationId: cors_graph_analysis
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: graph_analysis
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
        deadline: 60.0
      responses:
        "200":
          description: OK
  /graph-svc/api/v1/graph/briefing:
    options:
      operationId: cors_graph_briefing
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: graph_briefing
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
        deadline: 300.0
      responses:
        "200":
          description: OK
  /graph-svc/api/v1/graph/snapshots:
    options:
      operationId: cors_graph_snapshots
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: graph_snapshots
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/network/redistribution-recommendations:
    options:
      operationId: cors_network_redistribution_recommendations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: network_redistribution_recommendations
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /copilot-svc/api/v1/copilot/transfer:
    options:
      operationId: cors_copilot_transfer
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: copilot_transfer_recommendation
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/graph/dataset:
    options:
      operationId: cors_graph_dataset
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: graph_dataset_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
        deadline: 60.0
      responses:
        "200":
          description: OK
  /intake-svc/api/v1/extract:
    options:
      operationId: cors_intake_extract
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: intake_extract
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /intake-svc/api/v1/extract/document:
    options:
      operationId: cors_intake_document_extract
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: intake_extract_document
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/organizations/me:
    options:
      operationId: cors_organizations_me
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: organizations_me_get
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Current organization profile
    patch:
      operationId: organizations_me_patch
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Current organization profile updated
  /match-svc/api/v1/organizations:
    options:
      operationId: cors_organizations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: organizations_list
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Organization list
    post:
      operationId: organizations_create
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "201":
          description: Organization created
  /match-svc/api/v1/organizations/{organization_id}/members:
    parameters:
      - name: organization_id
        in: path
        required: true
        type: string
    options:
      operationId: cors_organization_members
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: organization_members_list
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Organization memberships
  /match-svc/api/v1/organizations/{organization_id}/verify:
    parameters:
      - name: organization_id
        in: path
        required: true
        type: string
    options:
      operationId: cors_organization_verify
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: organization_verify
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Organization verified
  /match-svc/api/v1/organizations/{organization_id}/members/{membership_id}/approve:
    parameters:
      - name: organization_id
        in: path
        required: true
        type: string
      - name: membership_id
        in: path
        required: true
        type: string
    options:
      operationId: cors_organization_membership_approve
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: organization_membership_approve
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Membership approved
  /match-svc/api/v1/organizations/{organization_id}/members/{membership_id}/role:
    parameters:
      - name: organization_id
        in: path
        required: true
        type: string
      - name: membership_id
        in: path
        required: true
        type: string
      - name: role
        in: query
        required: true
        type: string
    options:
      operationId: cors_organization_membership_role
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: organization_membership_role_update
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: Membership role updated
  /match-svc/api/v1/organizations/{organization_id}/region:
    parameters:
      - name: organization_id
        in: path
        required: true
        type: string
    options:
      operationId: cors_organization_region
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    patch:
      operationId: organization_region_update
      security:
        - firebase_auth: []
        - api_key: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: OK
  /match-svc/api/v1/reservations:
    options:
      operationId: cors_match_svc_api_v1_reservations
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: match_svc_api_v1_reservations_get
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK

  /match-svc/api/v1/inventory/expiry-risk:
    options:
      operationId: cors_match_svc_api_v1_inventory_expiry_risk
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    get:
      operationId: match_svc_api_v1_inventory_expiry_risk_get
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK

  /match-svc/api/v1/cases/{case_id}/outcomes:
    options:
      operationId: cors_match_svc_api_v1_cases_case_id_outcomes
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    parameters:
      - name: case_id
        in: path
        required: true
        type: string
    post:
      operationId: match_svc_api_v1_cases_case_id_outcomes_post
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK

  /match-svc/api/v1/auth/donor-profile/donations/complete:
    options:
      operationId: cors_match_svc_api_v1_auth_donor_profile_donations_complete
      security: []
      x-google-backend:
        address: ${local.gateway_backend_address}
        path_translation: APPEND_PATH_TO_ADDRESS
        disable_auth: false
      responses:
        "200":
          description: CORS preflight response
    post:
      operationId: match_svc_api_v1_auth_donor_profile_donations_complete_post
      security:
        - firebase_auth: []
        - api_key: []
      responses:
        "200":
          description: OK

EOT
      )
    }
  }
}

resource "google_api_gateway_gateway" "bloodnet" {
  provider = google-beta
  count    = var.gateway_enabled ? 1 : 0

  project    = var.project_id
  region     = var.gateway_region
  gateway_id = "bloodnet-gateway"
  api_config = google_api_gateway_api_config.bloodnet[0].id

  depends_on = [google_api_gateway_api_config.bloodnet]

  timeouts {
    create = "45m"
    update = "45m"
    delete = "45m"
  }
}
