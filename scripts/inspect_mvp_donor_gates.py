"""Read-only comparison of the two authorized donors' matching gates."""
import json
from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
from urllib.parse import urlsplit, unquote
from stage_mvp_candidate import run, PROJECT, REGION, DATABASE, ROOT


def main():
    parsed = urlsplit(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
    dsn = {"user": unquote(parsed.username), "password": unquote(parsed.password), "dbname": unquote(parsed.path.lstrip("/"))}
    report = {}
    with Connector(credentials=Credentials(run("auth", "print-access-token").stdout.strip()), refresh_strategy="LAZY") as connector:
        for label, database in (("live", dsn["dbname"]), ("candidate", DATABASE)):
            connection = connector.connect(f"{PROJECT}:{REGION}:bloodnet-postgres", "pg8000", user=dsn["user"], password=dsn["password"], db=database)
            try:
                cursor = connection.cursor()
                cursor.execute("SET TRANSACTION READ ONLY")
                report[label] = {}
                for role, email in (("donor1", "imzoya.shakeel@gmail.com"), ("donor2", "mitchell.tucker3214@gmail.com")):
                    cursor.execute("""SELECT u.status='active' AS active_user, u.email_verified,
                        d.blood_group IN ('O+','O-') AS compatible, d.availability='available' AS available,
                        d.consent_contact, d.notification_channels, d.eligibility_status='eligible' AS eligible_status,
                        d.date_of_birth <= CURRENT_DATE - INTERVAL '18 years' AS age_eligible,
                        d.next_eligible_at IS NULL OR d.next_eligible_at <= NOW() AS not_deferred,
                        l.lat IS NOT NULL AND l.lng IS NOT NULL AS has_location
                        FROM users u LEFT JOIN donor_profiles d ON d.user_id=u.id
                        LEFT JOIN user_locations l ON l.user_id=u.id WHERE u.email=%s""", (email,))
                    report[label][role] = dict(zip([column[0] for column in cursor.description], cursor.fetchone()))
            finally:
                connection.rollback()
                connection.close()
    (ROOT / "docs/mvp-donor-gate-comparison.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
