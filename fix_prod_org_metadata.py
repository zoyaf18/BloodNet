#!/usr/bin/env python
"""
Fix the live production organization metadata to include the canonical bank_id.

This script directly patches the blood-bank organization metadata in Cloud SQL
so that the approval endpoint recognizes the bank_id scope correctly.
"""

import os
import sys
import time
import psycopg
from psycopg import connect

# Get database URL from environment
db_url = os.environ.get('BLOODNET_DATABASE_URL')
if not db_url:
    print('ERROR: BLOODNET_DATABASE_URL environment variable not set')
    sys.exit(1)

# Replace the private IP with the public IP for external connection
# Private: 10.59.27.3:5432  -> Public: 34.14.139.135:5432
db_url = db_url.replace('10.59.27.3:5432', '34.14.139.135:5432')

print(f'Database URL (first 60 chars): {db_url[:60]}...')

# Connect with retries
max_retries = 10
for attempt in range(max_retries):
    try:
        print(f'\nAttempt {attempt + 1}/{max_retries}: Connecting to Cloud SQL...')
        conn = connect(db_url, connect_timeout=15)
        print('✓ Connected successfully')
        break
    except Exception as e:
        print(f'✗ Connection failed: {type(e).__name__}: {str(e)[:80]}')
        if attempt < max_retries - 1:
            wait_time = 3 * (2 ** attempt)
            print(f'  Waiting {wait_time}s before retry...')
            time.sleep(wait_time)
        else:
            print('✗ Max retries exhausted')
            sys.exit(1)

try:
    cur = conn.cursor()

    # Show BEFORE state
    print('\n--- BEFORE UPDATE ---')
    cur.execute("SELECT id, name, type, metadata FROM organizations WHERE type = 'blood_bank' ORDER BY name;")
    before_rows = cur.fetchall()
    for row in before_rows:
        org_id, name, org_type, metadata = row
        print(f'  {name}: metadata={metadata}')

    # Update: add bank_id to Live Heartbeat Blood Bank
    print('\n--- UPDATING ---')
    cur.execute(
        "UPDATE organizations SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('bank_id', 'BANK-001') "
        "WHERE type = 'blood_bank' AND name = 'Live Heartbeat Blood Bank' "
        "RETURNING id, name, type, metadata;"
    )
    updated_rows = cur.fetchall()
    if updated_rows:
        for row in updated_rows:
            org_id, name, org_type, metadata = row
            print(f'✓ Updated {name}: metadata={metadata}')
    else:
        print('⚠ No rows matched the update query')

    conn.commit()
    print('\n✓ Changes committed to database')

    # Show AFTER state
    print('\n--- AFTER UPDATE ---')
    cur.execute("SELECT id, name, type, metadata FROM organizations WHERE type = 'blood_bank' ORDER BY name;")
    after_rows = cur.fetchall()
    for row in after_rows:
        org_id, name, org_type, metadata = row
        print(f'  {name}: metadata={metadata}')

    # Verify the fix
    print('\n--- VERIFICATION ---')
    cur.execute(
        "SELECT metadata->'bank_id' as bank_id FROM organizations WHERE type = 'blood_bank' AND name = 'Live Heartbeat Blood Bank';"
    )
    result = cur.fetchone()
    if result and result[0]:
        bank_id = result[0]
        print(f'✓ Bank ID is now set to: {bank_id}')
        if bank_id == '"BANK-001"':
            print('✓ VERIFIED: bank_id correctly set to BANK-001')
        else:
            print(f'⚠ WARNING: Expected "BANK-001" but got {bank_id}')
    else:
        print('✗ FAILED: bank_id not found after update')

finally:
    cur.close()
    conn.close()
    print('\n✓ Connection closed')
