# BloodNet — GCP/Terraform Foundation

This is the low-cost GCP foundation for BloodNet.

Target region: `asia-south1`
Target demo budget: approximately `$250/month`

## What this creates

- Required GCP APIs
- Artifact Registry Docker repository
- Firestore native database
- BigQuery dataset with 90-day demo table expiry
- Versioned private Cloud Storage bucket
- Pub/Sub topics for request/case/recommendation/audit events
- Least-privilege-oriented runtime service account
- Secret Manager secret for the JWT signing key
- Optional Cloud Run API service
- Optional $250 monthly billing budget alert

Cloud SQL, always-on Vertex endpoints, Neo4j, and other potentially expensive resources are deliberately NOT provisioned here. They can be added only when the application actually needs them.

## Prerequisites

Install:

- Google Cloud CLI
- Terraform >= 1.8
- A GCP project with billing enabled
- Permission to enable APIs and create the resources above

Authenticate:

```powershell
gcloud auth application-default login
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT_ID
```

## Configure

Copy:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set:

- `project_id`
- globally unique `storage_bucket_name`
- `allowed_origins` (HTTPS origins only)

Do not commit `terraform.tfvars`.

## Verify before applying

```powershell
terraform init
terraform fmt -check
terraform validate
terraform plan
```

The plan should show the foundation resources only.

The MVP baseline does not require external SMS/WhatsApp credentials, clinical
system connectivity, blood-bank vendor connectivity, or optional messaging
webhook activation. Those integrations remain available as opt-in extensions;
set their Terraform variables and provider secrets only when the corresponding
integration rollout is ready.

## Apply

```powershell
terraform apply
```

Confirm with `yes`.

## Verify

```powershell
terraform output
gcloud artifacts repositories list --location=asia-south1
gcloud pubsub topics list
gcloud firestore databases list
gcloud storage buckets list
```

For BigQuery:

```powershell
bq ls
```

## Edge / API Gateway deployment (Item 7)

For the production edge tier, enable the gateway and Cloud Armor policy in Terraform:

```powershell
terraform apply \
  -var='gateway_enabled=true' \
  -var='project_id=YOUR_GCP_PROJECT_ID' \
  -var='region=asia-south1' \
  -var='allowed_origins=["https://app.example.com","https://admin.example.com"]'
```

This creates:

- Cloud Armor security policy with WAF rule blocks for XSS / SQL injection / canary attacks
- per-IP rate limiting (200 requests / 60s) with 429 deny on exceed
- API Gateway API and API config with an OpenAPI definition
- a gateway endpoint that routes to the backend URL

To verify deployment:

```powershell
gcloud compute security-policies list --project YOUR_GCP_PROJECT_ID
gcloud api-gateway apis list --project YOUR_GCP_PROJECT_ID
gcloud api-gateway gateways list --project YOUR_GCP_PROJECT_ID --location=asia-south1
```

## Cloud Run

For the manual release path, set secrets only in the current terminal and run
the repository deployment script:

```powershell
$env:TF_VAR_jwt_secret = "ENTER_VALUE_IN_TERMINAL"
$env:TF_VAR_database_password = "ENTER_VALUE_IN_TERMINAL"
.\scripts\deploy.ps1
Remove-Item Env:TF_VAR_jwt_secret,Env:TF_VAR_database_password
```

The script builds the frontend and container, pushes an immutable image digest,
applies the existing Terraform resources, and performs a Cloud Run health
check. It does not print or commit secret values.

Because this project uses VPC Service Controls, the deployment script uses the
`bloodnet-private-pool` Cloud Build worker pool by default. Use
`-WorkerPool` to select another private pool. `-UsePublicWorkerPool` is an
explicit opt-out and may fail while Cloud Build fetches its source bucket.

Before applying, the script imports the pre-existing KMS key ring, KMS key, and
Secret Manager rotation topic when they are absent from Terraform state. It
also creates and reviews a Terraform plan. The database secret replacement is
expected when switching Cloud Run from the Cloud SQL socket connector to the
private-IP connection required by the VPC Service Controls setup.

An API Gateway config replacement is separate from the Cloud Run fix. It is
usually caused by Terraform declaring `bloodnet-api-config-prod` while the
existing gateway still uses a generated ID such as
`bloodnet-api-config-prod-20260829-v3`. Replacing that config can briefly
remove or interrupt gateway routing while the gateway points at the new
config, and it can fail if the managed gateway still references the old one.
Without `-AllowDestructiveTerraformPlan`, the script fails safely before
`terraform apply`. Review the plan and use that switch only when the gateway
replacement is intentional.

The first foundation apply intentionally leaves Cloud Run disabled because the exact application container image must come from the verified BloodNet repository. Production Cloud Run also requires a JWT secret version in Secret Manager and explicit CORS origins.

After the foundation apply creates the secret, add its first version without placing the value in Terraform files or state:

```powershell
"REPLACE_WITH_A_LONG_RANDOM_SECRET" | gcloud secrets versions add bloodnet-jwt-secret --data-file=-
```

The application uses Gemini through `agent-svc` and accesses Vertex AI with
the Cloud Run runtime service account. The deployment uses the project's
Google Cloud billing/credit balance and does not require an AI Studio API key
or a Gemini Secret Manager secret.

Cloud Run accepts network ingress for authenticated push delivery but does not
grant `allUsers` the invoker role. Pub/Sub push delivery is authenticated with
the Pub/Sub service agent; browser traffic must use an authenticated request
path and application identity/RBAC.

Once the API image has been built and pushed:

```powershell
terraform apply `
  -var='deploy_cloud_run=true' `
  -var='cloud_run_image=asia-south1-docker.pkg.dev/YOUR_GCP_PROJECT_ID/bloodnet/bloodnet-api:latest'
```

The service is configured with:

- min instances: 0
- max instances: 3
- 1 vCPU
- 512 MiB
- runtime service account
- Vertex AI Gemini using the project runtime identity
- Pub/Sub event transport
- JWT verification secret from Secret Manager

This keeps the demo architecture cost-conscious.

## Cost guardrails

The target is approximately $250/month, but this is NOT a guaranteed bill. Actual cost depends on usage, especially Gemini/Vertex AI, Cloud Run requests, BigQuery scans, and storage.

The configuration intentionally:

- uses Cloud Run scale-to-zero
- limits Cloud Run max instances
- avoids always-on compute
- expires demo BigQuery tables after 90 days
- expires old object versions after 30 days
- does not provision Cloud SQL
- does not provision a dedicated Vertex AI endpoint
- does not provision paid graph infrastructure

If your billing account ID is known, set `billing_account_id` to create a $250 monthly budget resource.

## Safety

BloodNet's MVP uses synthetic data only. The production path must retain the deterministic eligibility/compatibility boundary and human approval gate defined in SPEC.md.

## Deployment locking

Run deployments one at a time from the repository root with `scripts/deploy.ps1`. The script holds an exclusive `bloodnet-deploy.lock` file for the full build, plan, apply, and health-check lifecycle and uses Terraform's normal state locking. If a deployment is interrupted, wait for its Terraform process to exit before starting another deployment; the lock file itself is not a stale lock and may remain on disk.
