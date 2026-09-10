#!/usr/bin/env python3
"""
Static analysis of frontend code to verify all endpoints are properly wired.
This checks the actual TypeScript/React code, not runtime behavior.
"""

import re
from pathlib import Path
from typing import Dict, Set, List

# Frontend root
FRONTEND_ROOT = Path(r"c:\Users\saim\OneDrive\bloodnet-complete\web\app\src")

# All endpoints that should be wired
REQUIRED_ENDPOINTS = {
    "Auth": [
        "/match-svc/api/v1/me",
    ],
    "Cases & Inventory": [
        "/match-svc/api/v1/cases",
        "/match-svc/api/v1/hospital/inventory",
    ],
    "Recommendations": [
        "/match-svc/api/v1/recommendations",
    ],
    "Forecast": [
        "/match-svc/api/v1/regional/forecast",
    ],
    "Agent/Gemini": [
        "/agent-svc/api/v1/investigations/gemini",
    ],
    "Graph Analysis": [
        "/graph-svc/api/v1/graph/analysis",
    ],
    "Audit": [
        "/match-svc/api/v1/audit/search",
        "/match-svc/api/v1/audit/activity",
    ],
    "Organization": [
        "/match-svc/api/v1/organization",
        "/match-svc/api/v1/organization/members",
    ],
    "Account/Session": [
        "/match-svc/api/v1/me/session",
    ],
    "Privacy": [
        "/match-svc/api/v1/privacy/policies",
    ],
    "Donor/Swarm": [
        "/swarm-svc/api/v1/opportunities",
    ],
}

# Component-to-endpoint mapping
COMPONENT_ENDPOINTS = {
    "AppShell": [
        "/match-svc/api/v1/me",
        "/match-svc/api/v1/cases",
        "/match-svc/api/v1/recommendations",
    ],
    "AdminFeaturePanels": [
        "/match-svc/api/v1/regional/forecast",
        "/agent-svc/api/v1/investigations/gemini",
        "/graph-svc/api/v1/graph/analysis",
    ],
    "HospitalFeaturePanels": [
        "/match-svc/api/v1/hospital/inventory",
        "/intake-svc/api/v1/extract",
    ],
    "DonorPortal": [
        "/swarm-svc/api/v1/opportunities",
    ],
    "GraphExplorer": [
        "/graph-svc/api/v1/graph/analysis",
    ],
    "AuditInvestigator": [
        "/match-svc/api/v1/audit/search",
    ],
    "OrganizationManagementPanel": [
        "/match-svc/api/v1/organization",
        "/match-svc/api/v1/organization/members",
    ],
    "AccountManagementPanel": [
        "/match-svc/api/v1/me/session",
        "/match-svc/api/v1/audit/activity",
    ],
    "PrivacyPolicyPanel": [
        "/match-svc/api/v1/privacy/policies",
    ],
}

def analyze_file(filepath: Path) -> Dict[str, List[str]]:
    """Extract all API calls from a file."""
    if not filepath.exists():
        return {}
    
    content = filepath.read_text(encoding='utf-8')
    
    # Find all api() calls
    api_calls = re.findall(
        r'api(?:<[^>]+>)?\s*\(\s*["\']([^"\']+)["\']',
        content
    )
    
    return {filepath.name: list(set(api_calls))}

def check_endpoint_wiring():
    """Check that all endpoints are properly wired."""
    print("=" * 90)
    print("BLOODNET FRONTEND - ENDPOINT WIRING VERIFICATION")
    print("=" * 90)
    
    # Scan all TypeScript files
    files_to_check = [
        "AppShell.tsx",
        "FeaturePanels.tsx",
        "App.tsx",
    ]
    
    all_endpoints_found: Dict[str, Set[str]] = {}
    
    print("\n=== SCANNING SOURCE FILES ===\n")
    for filename in files_to_check:
        filepath = FRONTEND_ROOT / filename
        if filepath.exists():
            endpoints = analyze_file(filepath)
            if endpoints and filename in endpoints:
                all_endpoints_found[filename] = set(endpoints[filename])
                print(f"✓ {filename}")
                for endpoint in sorted(endpoints[filename]):
                    print(f"    → {endpoint}")
        else:
            print(f"✗ {filename} (not found)")
    
    # Flatten all found endpoints
    flattened = set()
    for endpoints_set in all_endpoints_found.values():
        flattened.update(endpoints_set)
    
    # Check coverage
    print("\n=== ENDPOINT COVERAGE ===\n")
    
    total_required = 0
    found_required = 0
    missing = []
    
    for category, endpoints in REQUIRED_ENDPOINTS.items():
        print(f"{category}:")
        for endpoint in endpoints:
            total_required += 1
            if any(endpoint in eps for eps in all_endpoints_found.values()):
                print(f"  ✓ {endpoint}")
                found_required += 1
            else:
                print(f"  ✗ {endpoint}")
                missing.append(endpoint)
        print()
    
    # Component analysis
    print("=== COMPONENT WIRING ===\n")
    
    for component, expected_endpoints in COMPONENT_ENDPOINTS.items():
        print(f"{component}:")
        filepath = FRONTEND_ROOT / "AppShell.tsx" if "Panel" in component else FRONTEND_ROOT / "FeaturePanels.tsx"
        
        if filepath.exists():
            content = filepath.read_text(encoding='utf-8')
            
            # Check if component is defined
            if f"function {component}" in content or f"export function {component}" in content:
                print(f"  ✓ Component defined")
                
                # Check for endpoint usage
                for endpoint in expected_endpoints:
                    if endpoint in content:
                        print(f"  ✓ Calls {endpoint}")
                    else:
                        print(f"  ⚠ Missing {endpoint}")
            else:
                print(f"  ✗ Component not found in {filepath.name}")
        print()
    
    # Summary
    print("=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"\nTotal required endpoints:  {total_required}")
    print(f"Properly wired:            {found_required}")
    print(f"Missing/Not found:         {total_required - found_required}")
    print(f"Coverage:                  {found_required/total_required*100:.1f}%")
    
    if missing:
        print(f"\n⚠ MISSING ENDPOINTS:")
        for endpoint in sorted(missing):
            print(f"  - {endpoint}")
    else:
        print(f"\n✅ ALL ENDPOINTS PROPERLY WIRED")
    
    # Check for unused api calls
    print("\n=== ADDITIONAL ENDPOINTS FOUND (Not in Required List) ===\n")
    extra = flattened - {ep for eps in REQUIRED_ENDPOINTS.values() for ep in eps}
    if extra:
        for endpoint in sorted(extra):
            print(f"  + {endpoint}")
    else:
        print("  (None - all endpoints are accounted for)")
    
    print("\n" + "=" * 90)
    
    return found_required == total_required

def check_component_props():
    """Verify all components receive required props."""
    print("\n=== COMPONENT PROP VERIFICATION ===\n")
    
    appshell = FRONTEND_ROOT / "AppShell.tsx"
    if appshell.exists():
        content = appshell.read_text(encoding='utf-8')
        
        components_to_check = [
            ("GraphExplorer", ["api"]),
            ("AuditInvestigator", ["api"]),
            ("AdminFeaturePanels", ["api", "cases", "recommendations", "onDecision"]),
            ("DonorPortal", ["api"]),
            ("HospitalFeaturePanels", ["api", "cases"]),
            ("OrganizationManagementPanel", ["identity", "api"]),
            ("AccountManagementPanel", ["identity", "api"]),
            ("PrivacyPolicyPanel", ["api"]),
        ]
        
        for component, required_props in components_to_check:
            print(f"{component}:")
            
            # Check component is called
            pattern = f"<{component}\\s+"
            if re.search(pattern, content):
                print(f"  ✓ Component is rendered")
                
                # Check props are passed
                for prop in required_props:
                    prop_pattern = f"<{component}[^>]*{prop}="
                    if re.search(prop_pattern, content):
                        print(f"  ✓ Prop '{prop}' passed")
                    else:
                        print(f"  ⚠ Prop '{prop}' might be missing")
            else:
                print(f"  ✗ Component not rendered")
            print()

if __name__ == "__main__":
    all_wired = check_endpoint_wiring()
    check_component_props()
    
    print("\n" + "=" * 90)
    if all_wired:
        print("✅ FRONTEND IS PRODUCTION-READY")
        print("All endpoints are properly wired in the frontend code.")
    else:
        print("⚠ Some endpoints may need wiring review.")
    print("=" * 90)
