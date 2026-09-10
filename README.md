# BloodNet

BloodNet is a full-stack blood availability and fulfillment platform for hospitals, blood banks, regional administrators, donors, and auditors. The repository includes the backend services, shared contracts, web frontend, infrastructure, and validation assets needed to run the application end-to-end and verify its major workflows locally.

This codebase supports the complete blood-request lifecycle:
- hospital intake or demand submission,
- deterministic donor matching with eligibility gating,
- inventory reservation, transfer, and fulfillment tracking,
- regional review and approval workflows,
- donor outreach, response handling, and notification delivery,
- forecasting, network intelligence, and operational visibility,
- audit trails, role-based authorization, and deployment verification.

The web app disables browser caching for operational API responses and the HTML
app shell. Hashed JavaScript and CSS assets remain cacheable because each build
generates new filenames.

## What is implemented in this repository

### Core platform capabilities
- Authenticated intake for hospitals and other roles
- Role-aware authorization and resource scoping across hospital, bank, regional, donor, and auditor views
- Deterministic donor ranking and eligibility screening before scoring
- Inventory finding, reservation orchestration, transfers, and receipt handling
- Approval-gated execution for reservations, transfers, donor mobilization, and donation-drive recommendations
- Durable workflow state with PostgreSQL-backed projections and audit records
- Notification outbox and provider delivery support for in-app and external messaging
- Forecasting and blood-shortage monitoring through Blood Weather
- Regional network intelligence, graph analysis, and redistribution recommendations
- Donor-response handling, swarm escalation, and shortfall recalculation
- End-to-end verification scripts and regression suites for local and deployment validation

### Major workflows supported

#### 1. Intake and request handling
- Hospital request intake, including request metadata, urgency, blood group, component, and quantity
- Optional document extraction for PDF/plain-text intake flows
- Request normalization and case creation for downstream matching and approval paths

#### 2. Matching and eligibility
- Server-side donor pool resolution and eligibility checks
- Blood-group and compatibility filtering
- Donor scoring based on eligibility, response probability, travel time, and operational context
- Inventory-first matching with donor shortfall calculation for remaining units

#### 3. Inventory and reservations
- Inventory unit visibility by bank and region
- Reservation recommendation and approval handling
- Inventory transfer proposals and receipt tracking
- Case fulfillment updates after successful receipt or donor completion

#### 4. Donor mobilization and notifications
- Targeted donor notification flow for selected eligible donors
- Donor cohort sizing and swarm-style outreach decisions based on remaining shortfall
- Donor response handling and notification status tracking
- Donation-drive path for regional awareness notifications to hospitals, banks, and eligible contactable donors

#### 5. Forecasting and regional operational intelligence
- Regional forecast generation for future shortage pressure
- Forecast-driven recommendation creation for regional donation drives
- Network analysis for supply, transfer, and dependency relationships
- Redistribution recommendations from graph intelligence
- Public-safe dashboards and scoped operational views

#### 6. Approvals, audits, and governance
- Approval-gated recommendations for inventory, donor mobilization, transfer, and regional drive actions
- Human approval and rejection flow with audit persistence
- Case and recommendation projections exposed only to authorized roles
- Audit records for request, approval, execution, notification, and fulfillment activity

#### 7. Frontend role surfaces
- Hospital surface with case visibility, fulfillment tracking, and regional donation notices
- Bank surface with inventory, reservations, approvals, and operational notifications
- Donor surface with donor history, consent, notification activity, and outreach responses
- Regional admin and auditor surfaces for forecasts, recommendations, network health, and case review

#### 8. Integrations and extensibility
- Optional FHIR/HL7 normalization paths
- Optional signed SMS/WhatsApp intake and provider-based messaging adapters
- RAG-backed SOP search for recommendation evidence
- Travel-time abstraction with deterministic local provider and optional Google Routes adapter
- Inventory Copilot using constrained optimization for transfer planning

## Repository structure

### Application and service entrypoints
- `main.py` — unified FastAPI entrypoint that mounts the service applications under one runtime
- `Dockerfile` — containerization for the backend runtime
- `pyproject.toml` — Python package metadata and test configuration
- `requirements.txt` — Python dependency list for the environment

### Shared contracts and domain models
- `contracts/` — shared schemas, config, event contracts, auth helpers, inventory logic, audit support, and cross-service interfaces
  - `auth.py`, `auth_permissions.py`, `auth_repository.py`, `auth_utils.py`
  - `models.py`, `config.py`, `events.py`, `event_bus.py`, `event_dispatcher.py`
  - `inventory_payload.py`, `donor_outcome.py`, `travel_time.py`, `forecast.py`, `security_policy.py`

### Backend services
- `services/` — service implementations powering the platform
  - `agent-svc/`
  - `approval-svc/`
  - `copilot-svc/`
  - `execution-svc/`
  - `graph-svc/`
  - `intake-svc/`
  - `integration-svc/`
  - `match-svc/`
  - `notify-svc/`
  - `swarm-svc/`

### Web application
- `web/app/` — React + Vite frontend for hospital, bank, donor, regional, and auditor workflows

### Tests and validation
- `tests/` — shared fixtures and utilities
- `tests_e2e/` — end-to-end validation coverage
- `tests_phase2/` — phase 2 regression, durability, service boundary, and authorization validation
- Root-level `test_*.py` files — focused verification scripts for specific flows

### Data, infrastructure, and deployment support
- `migrations/` — database migration assets
- `infra/` — infrastructure and deployment definitions, including Terraform and support files
- `scripts/` — local setup, verification, deployment, and operational helper scripts
- `sim/` — simulation and behavior experiments
- `ml/` — forecasting and ML-related components

## Local development workflow
1. Create a local Python environment and install dependencies.
2. Use the scripts in `scripts/` for local setup, validation, or deployment preparation.
3. Run the backend via `main.py` or the appropriate service entrypoints.
4. Run the frontend from `web/app/` for UI validation.
5. Execute tests from `tests/`, `tests_phase2/`, or `tests_e2e/` as needed.

## Quick start

### Backend
```bash
python -m venv .venv
. .venv/bin/activate   # or .\.venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080
```

### Frontend
```bash
cd web/app
npm install
npm run dev
```

## Useful verification commands

Run targeted tests for major flows:

```bash
# intake and matching
python -m pytest tests_phase2/test_intake.py -q

# notification and outbox recovery
python -m pytest tests_phase2/test_notification_outbox_crash_recovery.py -q

# bank review and UI regressions
python -m pytest -q tests_phase2/test_bank_review_regressions.py tests_phase2/test_inventory_mutation.py tests_phase2/test_postgres_inventory_mutation.py tests_phase2/test_ui_flow.py tests_phase2/test_workflow_authorization.py
```

## Notes on the current implementation

- The runtime is a unified Cloud Run container with mounted service namespaces including `/match-svc`, `/swarm-svc`, `/intake-svc`, `/agent-svc`, `/graph-svc`, and `/copilot-svc`.
- The frontend is a Vite-based React app served from the same container when build artifacts are present.
- Approval-gated actions remain the state-changing boundary; recommendations and execution are separated for auditability.
- Notification delivery is durable and uses an outbox pattern where configured.
- Public and authenticated behaviors are separated by API gateway and role/resource checks.
- The codebase includes production-facing deployment configuration, but local-only credentials, logs, and validation artifacts are intentionally kept out of GitHub.

## Summary

BloodNet already contains a broad set of healthcare coordination features beyond a simple donor request flow, including authenticated user management, request intake, donor matching, inventory operations, forecasting, network intelligence, donor outreach, regional donation drives, approvals, audits, and operational dashboards. This README is intended to reflect the full implementation footprint currently present in the repository.

