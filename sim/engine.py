import simpy
import random
from datetime import datetime, timedelta
from typing import List, Dict

from contracts.models import (
    Request, BloodBank, Donor, BloodGroup, Component, Urgency, 
    RequestStatus, VerificationState, InventoryUnit, InventoryStatus, Hospital
)
from sim.generator import generate_banks, generate_hospitals, generate_donors
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "match-svc")))
from inventory_match import find_inventory_matches

class BloodNetSimulation:
    def __init__(self, env: simpy.Environment, banks: List[BloodBank], donors: List[Donor]):
        self.env = env
        self.banks = {b.bank_id: b for b in banks}
        self.donors = donors
        
        # Setup simulated banks as simpy stores/resources if needed
        self.stats = {"requests": 0, "fulfilled_locally": 0, "escalated": 0, "fulfilled_network": 0}

    def request_generator(self, arrival_rate_minutes: int = 30):
        """Generates random blood requests over time."""
        req_id = 1
        while True:
            yield self.env.timeout(random.expovariate(1.0 / arrival_rate_minutes))
            bg = random.choice([BloodGroup.O_POS, BloodGroup.A_POS, BloodGroup.B_POS])
            req = Request(
                request_id=f"REQ-{req_id:04d}",
                group=bg,
                component=Component.RBC,
                qty=random.randint(1, 4),
                hospital_id=f"H{random.randint(1, 18)}",
                urgency=Urgency.ROUTINE,
                required_by=datetime.utcnow() + timedelta(hours=4),
                source_channel="simulation",
                verification_state=VerificationState.VERIFIED,
                status=RequestStatus.OPEN
            )
            self.env.process(self.process_request(req))
            req_id += 1

    def process_request(self, req: Request):
        self.stats["requests"] += 1

        # Step 1: Synthesize mock available units in inventory at each bank
        now = datetime.utcnow()
        mock_units = []
        for b_id in self.banks.keys():
            for i in range(random.randint(0, 3)):
                mock_units.append(InventoryUnit(
                    unit_id=f"U-{b_id}-{i}-{random.randint(1000,9999)}",
                    bank_id=b_id,
                    group=req.group,
                    component=req.component,
                    collected_at=now - timedelta(days=2),
                    expires_at=now + timedelta(days=20),
                    status=InventoryStatus.AVAILABLE
                ))

        # Setup hospital object
        hospital = Hospital(
            hospital_id=req.hospital_id,
            name=f"Hospital {req.hospital_id}",
            geo=self.banks["B1"].geo,  # assume near B1
            tier="tier1"
        )

        # Run inventory matching logic
        result = find_inventory_matches(req, hospital, list(self.banks.values()), mock_units)

        if result.units_remaining == 0:
            self.stats["fulfilled_locally"] += 1
        elif result.units_from_inventory > 0:
            self.stats["fulfilled_network"] += 1
            self.stats["escalated"] += 1
        else:
            self.stats["escalated"] += 1

        # SimPy requires process functions to be generators (yield at least once).
        yield self.env.timeout(0)

def run_simulation(days: int = 7):
    print(f"Starting BloodNet Digital Twin (SimCity Pune) for {days} days...")
    banks = generate_banks()
    donors = generate_donors(100) # subset for speed during test
    
    env = simpy.Environment()
    sim = BloodNetSimulation(env, banks, donors)
    
    env.process(sim.request_generator())
    env.run(until=days * 24 * 60)
    
    print("Simulation Complete.")
    print("Stats:", sim.stats)

if __name__ == "__main__":
    run_simulation(30)

