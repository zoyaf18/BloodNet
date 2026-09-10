import os
import pandas as pd
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score
import joblib
import numpy as np

def create_dummy_data(n_samples: int = 10000) -> pd.DataFrame:
    """Generate synthetic donor history for model training."""
    np.random.seed(42)
    data = pd.DataFrame({
        "historical_response_rate": np.random.uniform(0.1, 1.0, n_samples),
        "historical_completion_rate": np.random.uniform(0.1, 1.0, n_samples),
        "days_since_last_donation": np.random.randint(30, 1000, n_samples),
        "age": np.random.randint(18, 65, n_samples),
        "is_repeat_donor": np.random.choice([0, 1], n_samples, p=[0.4, 0.6]),
        "distance_to_bank_km": np.random.exponential(5, n_samples),
        "travel_time_min": np.random.exponential(10, n_samples),
        "hour_of_day": np.random.randint(0, 24, n_samples),
        "day_of_week": np.random.randint(0, 7, n_samples),
        "urgency_level": np.random.choice([0, 1, 2], n_samples, p=[0.4, 0.4, 0.2]),
        "contact_fatigue_30d": np.random.randint(0, 5, n_samples),
        "group_scarcity_index": np.random.uniform(0, 1, n_samples),
    })
    
    # Target formulation: Will they show up and complete donation?
    # Logic: High response rate + short distance + recent donation = high probability
    prob = (data["historical_response_rate"] * 0.4 + 
            (1 / (1 + data["distance_to_bank_km"])) * 0.3 + 
            (1 / (1 + data["travel_time_min"])) * 0.1 +
            np.where(data["days_since_last_donation"] < 180, 0.2, 0.0) +
            data["is_repeat_donor"] * 0.1 -
            data["contact_fatigue_30d"] * 0.03 +
            data["group_scarcity_index"] * 0.05 +
            data["urgency_level"] * 0.02)
    
    # Add some noise
    prob = prob + np.random.normal(0, 0.1, n_samples)
    data["target"] = (prob > 0.5).astype(int)
    
    return data

def train_model():
    print("Generating synthetic data for Donor Success Model...")
    df = create_dummy_data()
    
    X = df.drop(columns=["target"])
    y = df["target"]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print("Training LightGBM model...")
    model = lgb.LGBMClassifier(n_estimators=100, learning_rate=0.1, random_state=42)
    model.fit(X_train, y_train)
    
    preds = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, preds)
    print(f"Model AUC: {auc:.4f}")
    
    os.makedirs(os.path.dirname(__file__), exist_ok=True)
    model_path = os.path.join(os.path.dirname(__file__), "model.joblib")
    joblib.dump(model, model_path)
    print(f"Model saved to {model_path}")

if __name__ == "__main__":
    train_model()
