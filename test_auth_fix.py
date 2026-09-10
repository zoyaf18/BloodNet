#!/usr/bin/env python3
"""Test fixed auth endpoints."""
import requests
import json
import time

# Wait for deployment to stabilize
time.sleep(20)

base = 'https://bloodnet-gateway-b01hqccm.uc.gateway.dev'
email = f'test-{int(time.time())}@example.com'
password = 'TestPass123!'

print('=== TESTING FIXED AUTH ENDPOINTS ===')
print()

# Test signup
print('1. POST /match-svc/api/v1/auth/signup')
try:
    r = requests.post(
        f'{base}/match-svc/api/v1/auth/signup',
        json={
            'email': email,
            'password': password,
            'password_confirm': password,
            'display_name': 'Test User',
            'phone': '+1-555-0100'
        },
        timeout=30
    )
    print(f'   Status: {r.status_code}')
    data = r.json()
    print(f'   Message: {data.get("message", "N/A")}')
    if data.get('status') == 'email_verified':
        print('   ✓ Account created and auto-verified (email not configured)')
    elif data.get('status') == 'email_unverified':
        print('   ○ Account created (awaiting email verification)')
    print()
except Exception as e:
    print(f'   ✗ Error: {type(e).__name__}: {e}')
    print()

# Test login
print('2. POST /match-svc/api/v1/auth/login')
try:
    r = requests.post(
        f'{base}/match-svc/api/v1/auth/login',
        json={'email': email, 'password': password},
        timeout=30
    )
    print(f'   Status: {r.status_code}')
    data = r.json()
    if r.status_code == 200:
        print(f'   ✓ Login successful')
        print(f'   Token: {data.get("access_token", "N/A")[:50]}...')
    else:
        print(f'   Error: {data.get("detail", "N/A")}')
    print()
except Exception as e:
    print(f'   ✗ Error: {type(e).__name__}: {e}')
    print()

print('=== SUMMARY ===')
print('If signup returns 202 with "email_verified": FIXED ✓')
print('If login returns 200 with JWT token: WORKING ✓')
