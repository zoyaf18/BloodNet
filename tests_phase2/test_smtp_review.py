import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from contracts.email_service import EmailDeliveryError, send_email


def test_smtp_brands_sender_and_tracks_acceptance(monkeypatch):
    for key, value in {"HOST": "smtp.example.invalid", "PORT": "587", "USERNAME": "sender", "PASSWORD": "test-only"}.items():
        monkeypatch.setenv(f"BLOODNET_SMTP_{key}", value)
    monkeypatch.setenv("BLOODNET_EMAIL_FROM", "sender@example.invalid")
    observed = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def starttls(self, **kwargs): observed.append("tls")
        def login(self, *args): observed.append("login")
        def send_message(self, message):
            observed.append(message)
            return {}
    monkeypatch.setattr("contracts.email_service.smtplib.SMTP", SMTP)
    message_id = send_email(recipient="recipient@example.invalid", subject="Test", text="Test", message_id="<review@example.invalid>")
    assert observed[:2] == ["tls", "login"]
    assert str(observed[2]["From"]) == "BloodNet <sender@example.invalid>"
    assert message_id == "<review@example.invalid>"


def test_missing_production_smtp_does_not_report_success_or_log_secrets(monkeypatch, capsys):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.delenv("BLOODNET_SMTP_HOST", raising=False)
    with pytest.raises(EmailDeliveryError):
        send_email(recipient="private@example.invalid", subject="Verify", text="private-token")
    assert "private" not in capsys.readouterr().out


def test_test_delivery_scope_fails_closed_before_connecting(monkeypatch):
    monkeypatch.setenv("BLOODNET_EMAIL_TEST_MODE", "true")
    monkeypatch.setenv("BLOODNET_EMAIL_TEST_RECIPIENTS", "allowed@example.invalid")
    monkeypatch.setattr("contracts.email_service.smtplib.SMTP", lambda *a, **kw: pytest.fail("Must not contact SMTP"))
    with pytest.raises(EmailDeliveryError, match="test delivery scope"):
        send_email(recipient="unapproved@example.invalid", subject="Test", text="Test")


def test_smtp_outreach_checks_consent_and_returns_provider_id(monkeypatch):
    module = importlib.import_module("notification_service")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "isolated-test")
    profile = {"consent_contact": True, "notification_channels": ["email"]}
    repository = SimpleNamespace(get_user_by_id=lambda _: SimpleNamespace(email="donor@example.invalid", email_verified=True, status="active"), get_donor_profile=lambda _: profile)
    monkeypatch.setattr("contracts.auth_repository.AuthRepository", lambda _: repository)
    monkeypatch.setattr("contracts.outreach_policy.current_recipient_allowed", lambda *args: True)
    sent = []
    monkeypatch.setattr("contracts.email_service.send_email", lambda **kwargs: sent.append(kwargs) or kwargs["message_id"])
    notification = SimpleNamespace(channel="email", donor_id=str(uuid4()), request_id="REVIEW-REQUEST", notification_id="REVIEW-SMTP", message="Approved test")
    result = module.SMTPNotificationProvider().send(notification)
    assert result.delivery_status == "accepted"
    assert result.provider_message_id == sent[0]["message_id"]
    profile["consent_contact"] = False
    with pytest.raises(RuntimeError):
        module.SMTPNotificationProvider().send(notification)
    assert len(sent) == 1
