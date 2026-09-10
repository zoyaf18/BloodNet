"""Read candidate case donor identities for browser acceptance mapping."""
import json
from urllib.parse import urlsplit, unquote

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials

from stage_mvp_candidate import run, PROJECT, REGION, DATABASE

parsed = urlsplit(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
with Connector(credentials=Credentials(run("auth", "print-access-token").stdout.strip()), refresh_strategy="LAZY") as connector:
    connection = connector.connect(
        f"{PROJECT}:{REGION}:bloodnet-postgres", "pg8000",
        user=unquote(parsed.username), password=unquote(parsed.password), db=DATABASE,
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT case_id, payload FROM workflow_cases WHERE case_id = %s", ("CASE-a7ee4b255ad7",))
        case = cursor.fetchone()
        cursor.execute("SELECT ranked_donors FROM workflow_case_projections WHERE case_id = %s", ("CASE-a7ee4b255ad7",))
        projection = cursor.fetchone()
        cursor.execute("SELECT id, email, display_name FROM users ORDER BY email")
        users = [{"id": str(row[0]), "email": row[1], "display_name": row[2]} for row in cursor.fetchall()]
        print(json.dumps({"case": case[1] if case else None, "ranked_donors": projection[0] if projection else [], "users": users}, default=str))
    finally:
        connection.close()
