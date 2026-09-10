"""Read-only aggregate reconciliation of isolated cloud acceptance data."""
import json
from urllib.parse import urlsplit, unquote
from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
from stage_mvp_candidate import run, PROJECT, REGION, DATABASE, ROOT

parsed = urlsplit(run('secrets','versions','access','latest','--secret=bloodnet-database-url').stdout.strip())
with Connector(credentials=Credentials(run('auth','print-access-token').stdout.strip()), refresh_strategy='LAZY') as connector:
    connection=connector.connect(f'{PROJECT}:{REGION}:bloodnet-postgres','pg8000',user=unquote(parsed.username),password=unquote(parsed.password),db=DATABASE)
    try:
        cursor=connection.cursor()
        cursor.execute('SET TRANSACTION READ ONLY')
        report={}
        queries={
          'outbox': "SELECT o.event_id,o.case_id,o.status,o.delivery_status,o.attempts,o.payload->>'channel' AS channel,o.payload->>'donor_id'='system' AS system_recipient, n.notification_id IS NOT NULL AS has_notification FROM notification_outbox o LEFT JOIN notifications n ON n.notification_id=o.notification_id ORDER BY o.created_at",
          'stock': "SELECT payload->>'status' AS status,count(*) FROM inventory_units GROUP BY 1",
          'cases': "SELECT case_id,payload->>'outcome' AS outcome,payload->>'units_from_donors_fulfilled' AS donor_commitments,payload->>'confirmed_inventory_units' AS inventory_received,payload->>'confirmed_donor_units' AS donor_received FROM workflow_cases",
          'audit': "SELECT payload->>'action' AS action,count(*) FROM audit_records GROUP BY 1 ORDER BY 1",
        }
        for name,sql in queries.items():
            cursor.execute(sql)
            report[name]=[dict(zip([col[0] for col in cursor.description],row)) for row in cursor.fetchall()]
        (ROOT/'docs/mvp-final-database.json').write_text(json.dumps(report,indent=2,default=str))
        print(json.dumps(report,default=str))
    finally:
        connection.rollback()
        connection.close()
