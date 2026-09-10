#!/usr/bin/env python
"""
Direct database approach to record donor responses.
"""
import psycopg
from datetime import datetime, timezone

db_url = "postgresql://bloodnet:bloodnet_local@34.14.139.135:5432/bloodnet"
case_id = "CASE-4e1bd0ea6b4d"

try:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Step 1: Get the notifications for this case
            print(f"=== Retrieving notifications for case {case_id} ===")
            cur.execute("""
                SELECT notification_id, payload->'donor_id' as donor_id
                FROM notifications 
                WHERE payload->>'case_id' = %s
            """, (case_id,))
            notifications = cur.fetchall()
            print(f"Found {len(notifications)} notifications:")
            for notif_id, donor_id_str in notifications:
                print(f"  {notif_id}: {donor_id_str}")
            
            # Step 2: Record responses for both donors
            print(f"\n=== Recording donor responses ===")
            for notif_id, donor_id_str in notifications:
                # Skip the system notification
                if donor_id_str == 'system':
                    print(f"  Skipping system notification {notif_id}")
                    continue
                # Save response record
                cur.execute("""
                    INSERT INTO donor_responses (outreach_id, donor_id, response, responded_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (outreach_id, donor_id) DO UPDATE
                    SET response = EXCLUDED.response, responded_at = NOW()
                """, (notif_id, donor_id_str, 'accept', datetime.now(timezone.utc)))
                print(f"  Recorded accept for {donor_id_str}")
            
            conn.commit()
            
            # Step 3: Verify the responses were recorded
            print(f"\n=== Verifying responses in database ===")
            cur.execute("""
                SELECT outreach_id, donor_id, response
                FROM donor_responses 
                WHERE outreach_id IN (
                    SELECT notification_id FROM notifications 
                    WHERE payload->>'case_id' = %s
                )
            """, (case_id,))
            responses = cur.fetchall()
            for outreach_id, donor_id, response in responses:
                print(f"  {outreach_id}: {donor_id} = {response}")
            
    print(f"\n✓ Donor responses recorded successfully")
    print(f"Total: {len(notifications)} responses recorded")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
