import random
import uuid
from datetime import datetime, timedelta
import numpy as np

from contracts.models import BloodBank, BloodGroup, Donor, GeoPoint, Hospital

# SimCity Pune bounding box approx (18.45 to 18.60 Lat, 73.75 to 73.95 Lng)
PUNE_LAT_MIN, PUNE_LAT_MAX = 18.45, 18.60
PUNE_LNG_MIN, PUNE_LNG_MAX = 73.75, 73.95

# India blood group distribution approx
GROUP_DIST = {
    BloodGroup.O_POS: 0.37,
    BloodGroup.B_POS: 0.32,
    BloodGroup.A_POS: 0.22,
    BloodGroup.AB_POS: 0.06,
    BloodGroup.O_NEG: 0.015,
    BloodGroup.B_NEG: 0.007,
    BloodGroup.A_NEG: 0.005,
    BloodGroup.AB_NEG: 0.003,
}

def random_geo() -> GeoPoint:
    return GeoPoint(
        lat=random.uniform(PUNE_LAT_MIN, PUNE_LAT_MAX),
        lng=random.uniform(PUNE_LNG_MIN, PUNE_LNG_MAX)
    )

def generate_donors(count: int = 35000) -> list[Donor]:
    donors = []
    groups = [g.value for g in GROUP_DIST.keys()]
    probs = list(GROUP_DIST.values())
    
    # Normalize probabilities to exactly 1.0 to avoid numpy errors
    total = sum(probs)
    probs = [p / total for p in probs]
    
    chosen_groups = np.random.choice(groups, size=count, p=probs)
    now = datetime.utcnow()
    
    for i in range(count):
        # Latent reliability features for simulation
        responsiveness = np.clip(np.random.normal(0.6, 0.2), 0.1, 0.95)
        reliability = np.clip(np.random.normal(0.7, 0.15), 0.1, 0.95)
        
        last_donation = now - timedelta(days=random.randint(30, 800)) if random.random() > 0.3 else None
        
        donor = Donor(
            donor_id=f"D{i+1:05d}",
            blood_group=BloodGroup(str(chosen_groups[i])),
            geo=random_geo(),
            contact_tokens=[f"token_{uuid.uuid4().hex[:8]}"],
            last_donation_at=last_donation,
            deferral_flags=[],
            consent_scopes=["contactable", "analytics"],
            reliability_features={
                "true_responsiveness": responsiveness,
                "true_reliability": reliability,
                "historical_response_rate": responsiveness * random.uniform(0.8, 1.0),
                "historical_completion_rate": reliability * random.uniform(0.8, 1.0)
            }
        )
        donors.append(donor)
    return donors

def generate_banks(count: int = 5) -> list[BloodBank]:
    banks = []
    for i in range(count):
        bank = BloodBank(
            bank_id=f"B{i+1}",
            name=f"Pune Bank {i+1}",
            geo=random_geo(),
            licence_id=f"LIC-PN-{1000+i}",
            storage_capacity={"RBC": 500, "Platelets (RDP)": 50, "FFP": 200},
            min_reserve={"O+|RBC": 20, "A+|RBC": 15},
            operating_hours="24x7"
        )
        banks.append(bank)
    return banks

def generate_hospitals(count: int = 18, bank_ids: list[str] = None) -> list[Hospital]:
    hospitals = []
    bank_ids = bank_ids or [f"B{i+1}" for i in range(5)]
    for i in range(count):
        tier = "tier1" if i < 3 else "tier2" if i < 10 else "tier3"
        hospitals.append(Hospital(
            hospital_id=f"H{i+1}",
            name=f"Pune Hospital {i+1}",
            geo=random_geo(),
            tier=tier,
            affiliated_banks=random.sample(bank_ids, k=random.randint(1, min(3, len(bank_ids)))),
            demand_profile={"RBC_daily_avg": random.uniform(5, 20)}
        ))
    return hospitals

if __name__ == "__main__":
    print("Generating SimCity Pune dataset...")
    banks = generate_banks()
    hospitals = generate_hospitals(bank_ids=[b.bank_id for b in banks])
    donors = generate_donors(35000)
    print(f"Generated {len(banks)} banks, {len(hospitals)} hospitals, {len(donors)} donors.")
