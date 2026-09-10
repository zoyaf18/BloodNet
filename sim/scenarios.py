class SimulationScenario:
    def __init__(self, name: str, intensity: float, description: str):
        self.name = name
        self.intensity = intensity
        self.description = description

SCENARIOS = {
    "baseline": SimulationScenario("Baseline", 1.0, "Normal daily operations in Pune."),
    "dengue_outbreak": SimulationScenario("Dengue Outbreak", 2.5, "High demand for Platelets (RDP)."),
    "mass_casualty": SimulationScenario("Mass Casualty Event", 5.0, "Sudden spike in O- and general RBC demand.")
}

def get_scenario(name: str) -> SimulationScenario:
    return SCENARIOS.get(name, SCENARIOS["baseline"])
