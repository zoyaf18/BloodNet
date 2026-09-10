from contracts.capabilities import capability_report


def test_capability_report_uses_frugal_fallbacks_without_claiming_providers():
    report = capability_report({"BLOODNET_ENV": "local"})

    assert report["capabilities"]["travel_estimation"]["operational"] is True
    assert report["capabilities"]["travel_estimation"]["mode"] == "haversine fallback"
    assert report["capabilities"]["document_intake"]["operational"] is True
    assert report["capabilities"]["rag"]["operational"] is False
    assert report["capabilities"]["messaging_delivery"]["operational"] is False


def test_capability_report_marks_fully_configured_adapters_ready():
    report = capability_report({
        "BLOODNET_ENV": "production",
        "BLOODNET_DATABASE_URL": "postgresql://configured",
        "GOOGLE_CLOUD_PROJECT": "bloodnet-project",
        "BLOODNET_AGENT_RUNTIME": "vertex_agent_engine",
        "BLOODNET_AGENT_ENGINE_ID": "projects/example/locations/example/reasoningEngines/example",
        "BLOODNET_RAG_ENABLED": "true",
        "BLOODNET_TRAVEL_TIME_PROVIDER": "google_routes",
        "GOOGLE_MAPS_API_KEY": "configured",
        "BLOODNET_MESSAGING_WEBHOOK_SECRET": "configured",
        "BLOODNET_ENABLE_NOTIFICATIONS": "true",
        "BLOODNET_NOTIFICATION_PROVIDER_URL": "https://provider.invalid/send",
        "BLOODNET_NOTIFICATION_PROVIDER_TOKEN": "configured",
        "BLOODNET_NOTIFICATION_RECEIPT_SECRET": "configured",
        "BLOODNET_CLOUD_TASKS_QUEUE": "notifications",
        "BLOODNET_NOTIFICATION_DELIVERY_URL": "https://api.invalid/internal/notifications/deliver",
        "BLOODNET_RUNTIME_SERVICE_ACCOUNT": "runtime@example.invalid",
    })

    assert report["summary"] == {"ready": 8, "total": 8}
    assert all(item["operational"] for item in report["capabilities"].values())
