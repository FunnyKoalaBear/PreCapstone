#reaches postgres database and makes changes
import psycopg2 as postgres #postgres connection
import os 
from dotenv import load_dotenv #reads .env file 

load_dotenv()
print(os.getenv("DB_Name"))


def print_rows(cur):
    headers = [col.name for col in cur.description]
    rows = [[str(value) for value in row] for row in cur.fetchall()]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]

    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
    print(f"({len(rows)} rows)")

conn = postgres.connect(
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    dbname=os.getenv("DB_NAME"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
)
cur = conn.cursor() #cursor performs operations on db 

cur.execute("SELECT * FROM scans")
print_rows(cur)

print("NEW LINE")


cur.execute("INSERT INTO scans (uid) VALUES (%s)", ("NEW ID",))
conn.commit()


print("NEW LINE") 

cur.execute("SELECT * FROM scans")
print_rows(cur)