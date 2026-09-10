#!/usr/bin/env python3
"""
Comprehensive frontend endpoint verification script.
Tests that all API endpoints used by the frontend are properly wired.
"""

import json
import sys
import subprocess
from typing import Any, Dict

# Gateway URL (from deployment)
GATEWAY_URL = "https://bloodnet-gateway-b01hqccm.uc.gateway.dev"

# All endpoints used by the frontend
ENDPOINTS = {
    "auth": [
        "/match-svc/api/v1/me",
    ],
    "cases": [
        "/match-svc/api/v1/cases",
        "/match-svc/api/v1/hospital/inventory",
    ],
    "recommendations": [
        "/match-svc/api/v1/recommendations",
    ],
    "forecast": [
        "/match-svc/api/v1/regional/forecast",
    ],
    "agent": [
        "/agent-svc/api/v1/investigations/gemini",
    ],
    "graph": [
        "/graph-svc/api/v1/graph/analysis",
    ],
    "audit": [
        "/match-svc/api/v1/audit/search",
        "/match-svc/api/v1/audit/activity",
    ],
    "organization": [
        "/match-svc/api/v1/organization",
        "/match-svc/api/v1/organization/members",
    ],
    "account": [
        "/match-svc/api/v1/me/session",
    ],
    "privacy": [
        "/match-svc/api/v1/privacy/policies",
    ],
    "donor": [
        "/swarm-svc/api/v1/opportunities",
    ],
}

def check_gateway() -> bool:
    """Verify the gateway is responding."""
    print("\n=== GATEWAY HEALTH ===")
    try:
        result = subprocess.run(
            [
                "powershell",
                "-Command",
                f"$r = Invoke-WebRequest -Uri '{GATEWAY_URL}/health' -UseBasicParsing; $r.StatusCode"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✓ Gateway responding: {result.stdout.strip()}")
            return True
        else:
            print(f"✗ Gateway check failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"✗ Gateway unreachable: {e}")
        return False

def check_endpoint(method: str, endpoint: str, category: str) -> bool:
    """Test a single endpoint."""
    url = f"{GATEWAY_URL}{endpoint}"
    
    try:
        # Use OPTIONS or GET with minimal payload
        if method == "GET":
            cmd = f'$r = Invoke-WebRequest -Uri "{url}" -Method GET -UseBasicParsing -TimeoutSec 3; $r.StatusCode'
        else:
            cmd = f'$r = Invoke-WebRequest -Uri "{url}" -Method OPTIONS -UseBasicParsing -TimeoutSec 3; $r.StatusCode'
        
        result = subprocess.run(
            ["powershell", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        status_code = result.stdout.strip()
        
        # Accept 200, 201, 400, 404, etc. - we just want to know the service is responding
        if result.returncode == 0 and status_code:
            print(f"  ✓ {endpoint:<50} → {status_code}")
            return True
        else:
            print(f"  ✗ {endpoint:<50} → {result.stderr[:80]}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"  ⏱ {endpoint:<50} → TIMEOUT")
        return False
    except Exception as e:
        print(f"  ✗ {endpoint:<50} → {str(e)[:60]}")
        return False

def check_endpoints() -> Dict[str, bool]:
    """Check all endpoints by category."""
    print("\n=== ENDPOINT AVAILABILITY ===\n")
    
    results = {}
    total = 0
    passing = 0
    
    for category, endpoints in ENDPOINTS.items():
        print(f"{category.upper()}:")
        results[category] = {}
        
        for endpoint in endpoints:
            total += 1
            is_up = check_endpoint("GET", endpoint, category)
            results[category][endpoint] = is_up
            if is_up:
                passing += 1
    
    print(f"\n=== SUMMARY ===")
    print(f"Total endpoints: {total}")
    print(f"Passing: {passing}")
    print(f"Failing: {total - passing}")
    print(f"Pass rate: {passing/total*100:.1f}%")
    
    return results

def verify_frontend_code() -> None:
    """Verify frontend code has proper API calls."""
    print("\n=== FRONTEND CODE VERIFICATION ===\n")
    
    import os
    
    app_shell = os.path.join(os.path.dirname(__file__), "web/app/src/AppShell.tsx")
    feature_panels = os.path.join(os.path.dirname(__file__), "web/app/src/FeaturePanels.tsx")
    
    checks = [
        (app_shell, "AppShell.tsx", [
            "api",
            "match-svc",
            "OrganizationManagementPanel",
            "AccountManagementPanel",
            "PrivacyPolicyPanel",
        ]),
        (feature_panels, "FeaturePanels.tsx", [
            "GraphExplorer",
            "AuditInvestigator",
            "graph-svc",
            "audit/search",
            "/match-svc/api/v1",
        ]),
    ]
    
    for filepath, name, keywords in checks:
        if os.path.exists(filepath):
            with open(filepath) as f:
                content = f.read()
            
            missing = []
            for keyword in keywords:
                if keyword not in content:
                    missing.append(keyword)
            
            if missing:
                print(f"✗ {name}: Missing keywords: {missing}")
            else:
                print(f"✓ {name}: All required keywords present")
        else:
            print(f"✗ {name}: File not found")

def main():
    print("=" * 80)
    print("BLOODNET FRONTEND ENDPOINT VERIFICATION")
    print("=" * 80)
    
    # Check gateway
    if not check_gateway():
        print("\n⚠ Gateway is not responding. Endpoints may not be available.")
        print("Note: This is expected if the backend is not deployed.")
    
    # Check endpoints
    results = check_endpoints()
    
    # Verify frontend code
    verify_frontend_code()
    
    # Summary
    print("\n" + "=" * 80)
    total_categories = len(results)
    healthy_categories = sum(1 for cat_results in results.values() if all(cat_results.values()))
    
    print(f"CATEGORIES: {healthy_categories}/{total_categories} fully functional")
    print("=" * 80)
    
    # Check for any critical missing endpoints
    critical = [
        "/match-svc/api/v1/me",
        "/match-svc/api/v1/cases",
        "/graph-svc/api/v1/graph/analysis",
        "/match-svc/api/v1/audit/search",
    ]
    
    missing_critical = []
    for endpoint in critical:
        found = False
        for cat_results in results.values():
            if endpoint in cat_results and cat_results[endpoint]:
                found = True
                break
        if not found:
            missing_critical.append(endpoint)
    
    if missing_critical:
        print(f"\n⚠ CRITICAL ENDPOINTS MISSING: {missing_critical}")
    else:
        print("\n✓ All critical endpoints are available")

if __name__ == "__main__":
    main()
