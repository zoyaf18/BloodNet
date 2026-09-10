import os
import joblib
import pandas as pd
from datetime import datetime, timezone
from typing import List

from contracts.models import Donor

_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "ml", "donor_success", "model.joblib")
_MODEL = None
DEFAULT_AGE_YEARS = 40

FEATURE_NAMES = (
    "historical_response_rate", "historical_completion_rate",
    "days_since_last_donation", "age", "is_repeat_donor",
    "distance_to_bank_km", "travel_time_min", "hour_of_day", "day_of_week",
    "urgency_level", "contact_fatigue_30d", "group_scarcity_index",
)

def get_model():
    global _MODEL
    if _MODEL is None:
        if os.path.exists(_MODEL_PATH):
            _MODEL = joblib.load(_MODEL_PATH)
            if hasattr(_MODEL, "set_params"):
                _MODEL.set_params(n_jobs=1)
        else:
            raise RuntimeError("Model not found. Run training script first.")
    return _MODEL

def _build_features(donors: List[Donor]) -> list[dict]:
    """Build the single feature contract used by scoring and explanations."""
    features = []
    for d in donors:
        rel = d.reliability_features or {}
        days_since = rel.get("days_since_last_donation")
        is_repeat = rel.get("is_repeat_donor")
        if days_since is None and d.last_donation_at:
            donation_at = d.last_donation_at
            if donation_at.tzinfo is None:
                donation_at = donation_at.replace(tzinfo=timezone.utc)
            days_since = (datetime.now(timezone.utc) - donation_at).days
        if days_since is None:
            # Default to a conservative assumption for first-time or new donors.
            days_since = 365  # One year since "last" donation (a safe guess for new donors)
            is_repeat = 0
        if is_repeat is None:
            is_repeat = int(d.last_donation_at is not None)
        age = d.age_years if d.age_years is not None else rel.get("age", DEFAULT_AGE_YEARS)
        distance = rel.get("distance_to_bank_km")
        if distance is None:
            raise ValueError(f"Donor {d.donor_id} is missing distance_to_bank_km")
        features.append({
            "historical_response_rate": rel.get("historical_response_rate", 0.5),
            "historical_completion_rate": rel.get("historical_completion_rate", 0.5),
            "days_since_last_donation": days_since, "age": age,
            "is_repeat_donor": is_repeat, "distance_to_bank_km": distance,
            "travel_time_min": rel.get("travel_time_min", distance / 30.0 * 60.0),
            "hour_of_day": rel.get("hour_of_day", 12), "day_of_week": rel.get("day_of_week", 0),
            "urgency_level": rel.get("urgency_level", 0),
            "contact_fatigue_30d": rel.get("contact_fatigue_30d", 0),
            "group_scarcity_index": rel.get("group_scarcity_index", 0.5),
        })
    return features


def score_donors(donors: List[Donor]) -> list[float]:
    """Score donors using the ML model. Returns list of probabilities between 0 and 1."""
    features = _build_features(donors)
    if not features:
        return []
    unique_features: list[dict] = []
    feature_indexes: dict[tuple[object, ...], int] = {}
    donor_feature_indexes: list[int] = []
    for feature in features:
        key = tuple(feature[name] for name in FEATURE_NAMES)
        feature_index = feature_indexes.get(key)
        if feature_index is None:
            feature_index = len(unique_features)
            feature_indexes[key] = feature_index
            unique_features.append(feature)
        donor_feature_indexes.append(feature_index)
    df = pd.DataFrame(unique_features)
    model = get_model()
    probabilities = model.predict_proba(df)[:, 1].tolist()
    return [probabilities[index] for index in donor_feature_indexes]


def calibration_metrics(predictions: List[float], outcomes: List[int], bins: int = 10) -> dict[str, float]:
    """Calculate Brier score and expected calibration error for model checks."""
    if len(predictions) != len(outcomes) or not predictions:
        raise ValueError("predictions and outcomes must be non-empty and have equal length")
    brier = sum((prediction - outcome) ** 2 for prediction, outcome in zip(predictions, outcomes)) / len(predictions)
    ece = 0.0
    for bucket in range(bins):
        members = [index for index, prediction in enumerate(predictions)
                   if bucket / bins <= prediction < (bucket + 1) / bins or
                   bucket == bins - 1 and prediction == 1]
        if members:
            ece += len(members) / len(predictions) * abs(
                sum(predictions[index] for index in members) / len(members) -
                sum(outcomes[index] for index in members) / len(members)
            )
    return {"brier_score": brier, "ece": ece}


def score_donors_with_explanations(donors: List[Donor], top_k: int = 5) -> list[dict]:
    """Return scores plus deterministic feature reasons for admin inspection."""
    features = _build_features(donors)
    probabilities = score_donors(donors)
    results = []
    for donor, feature_row, probability in zip(donors, features, probabilities):
        reasons = sorted(
            ((name, float(value)) for name, value in feature_row.items()
             if name in {"historical_response_rate", "historical_completion_rate", "distance_to_bank_km", "contact_fatigue_30d"}),
            key=lambda item: abs(item[1] - 0.5), reverse=True,
        )
        results.append({
            "donor_id": donor.donor_id,
            "probability": probability,
            "model_version": "local-model-v1",
            "shap_reasons": [{"feature": name, "value": value} for name, value in reasons[:top_k]],
        })
    return results
