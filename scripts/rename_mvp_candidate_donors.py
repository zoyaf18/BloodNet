"""Give the two seeded donor users unique candidate-only Firebase email aliases."""
from urllib.parse import urlsplit, unquote

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials

from stage_mvp_candidate import run, PROJECT, REGION, DATABASE

parsed = urlsplit(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
updates = (
    ("imzoya.shakeel@gmail.com", "mvp-donor-1@bloodnet.local"),
    ("mitchell.tucker3214@gmail.com", "mvp-donor-2@bloodnet.local"),
)
with Connector(credentials=Credentials(run("auth", "print-access-token").stdout.strip()), refresh_strategy="LAZY") as connector:
    connection = connector.connect(
        f"{PROJECT}:{REGION}:bloodnet-postgres", "pg8000",
        user=unquote(parsed.username), password=unquote(parsed.password), db=DATABASE,
    )
    try:
        cursor = connection.cursor()
        for old_email, new_email in updates:
            cursor.execute("UPDATE users SET email = %s WHERE email = %s RETURNING id", (new_email, old_email))
            if not cursor.fetchone():
                raise RuntimeError(f"Candidate donor not found: {old_email}")
        connection.commit()
        print("Updated two candidate donor email aliases")
    finally:
        connection.close()
