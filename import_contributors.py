"""
Import your master contributor list (Excel or CSV) into the contributors table.

Usage:
    python import_contributors.py path/to/master_list.xlsx

Expects columns (case-insensitive, flexible matching) roughly like:
    AMB ID / AMB#### / Unique ID
    Name
    Phone / Mobile / Phone Number
    Village / Area   (optional)
    Photo URL / Photo Link   (optional)

Set the DATABASE_URL environment variable before running, e.g.:
    set DATABASE_URL=postgres://...   (Windows cmd)
    export DATABASE_URL=postgres://...   (Mac/Linux)
"""
import os
import sys
import pandas as pd
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")

COLUMN_MAP_HINTS = {
    "amb_id": ["amb", "unique id", "uid", "amb_id", "amb id"],
    "name": ["name", "contributor name", "full name"],
    "phone": ["phone", "mobile", "phone number", "contact"],
    "village": ["village", "area", "location", "place"],
    "photo_url": ["photo url", "photo link", "image url"],
    "txn_date": ["date"],
    "amount": ["amount"],
    "txn_no": ["transaction no", "txn no", "transaction number"],
}


def guess_column(columns, hints):
    lower_cols = {c.lower().strip(): c for c in columns}
    for hint in hints:
        for lc, orig in lower_cols.items():
            if hint in lc:
                return orig
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_contributors.py path/to/master_list.xlsx")
        sys.exit(1)
    if not DATABASE_URL:
        print("ERROR: set DATABASE_URL environment variable first.")
        sys.exit(1)

    path = sys.argv[1]
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path, dtype=str)
    else:
        df = pd.read_excel(path, dtype=str)

    df.columns = [str(c).strip() for c in df.columns]
    col_amb = guess_column(df.columns, COLUMN_MAP_HINTS["amb_id"])
    col_name = guess_column(df.columns, COLUMN_MAP_HINTS["name"])
    col_phone = guess_column(df.columns, COLUMN_MAP_HINTS["phone"])
    col_village = guess_column(df.columns, COLUMN_MAP_HINTS["village"])
    col_photo = guess_column(df.columns, COLUMN_MAP_HINTS["photo_url"])
    col_date = guess_column(df.columns, COLUMN_MAP_HINTS["txn_date"])
    col_amount = guess_column(df.columns, COLUMN_MAP_HINTS["amount"])
    col_txnno = guess_column(df.columns, COLUMN_MAP_HINTS["txn_no"])

    print("Detected columns:")
    print(f"  AMB ID  -> {col_amb}")
    print(f"  Name    -> {col_name}")
    print(f"  Phone   -> {col_phone}")
    print(f"  Village -> {col_village}")
    print(f"  Photo   -> {col_photo}")
    print(f"  Date    -> {col_date}")
    print(f"  Amount  -> {col_amount}")
    print(f"  Txn No  -> {col_txnno}")

    if not col_amb:
        print("ERROR: could not detect a Unique/AMB ID column. Aborting.")
        sys.exit(1)

    confirm = input("Proceed with import using these columns? (y/n): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cur = conn.cursor()

    inserted, skipped, unnamed = 0, 0, 0
    for _, row in df.iterrows():
        amb_id = str(row.get(col_amb, "")).strip() if col_amb else None
        amb_id = None if amb_id in (None, "", "nan") else amb_id
        if not amb_id:
            skipped += 1
            continue

        name = str(row.get(col_name, "")).strip() if col_name else ""
        if not name or name.lower() == "nan":
            name = f"(Name not recorded - {amb_id})"
            unnamed += 1
        phone = str(row.get(col_phone, "")).strip() if col_phone else None
        village = str(row.get(col_village, "")).strip() if col_village else None
        photo_url = str(row.get(col_photo, "")).strip() if col_photo else None
        txn_date = str(row.get(col_date, "")).strip() if col_date else None
        amount = str(row.get(col_amount, "")).strip() if col_amount else None
        txn_no = str(row.get(col_txnno, "")).strip() if col_txnno else None

        phone = None if phone in (None, "", "nan") else phone
        village = None if village in (None, "", "nan") else village
        photo_url = None if photo_url in (None, "", "nan") else photo_url
        txn_date = None if txn_date in (None, "", "nan") else txn_date.split(" ")[0]
        amount = None if amount in (None, "", "nan") else amount
        txn_no = None if txn_no in (None, "", "nan") else txn_no

        cur.execute("""
            INSERT INTO contributors (amb_id, name, phone, village, photo_url, txn_date, amount, txn_no)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (amb_id) DO UPDATE SET
                name = EXCLUDED.name,
                phone = EXCLUDED.phone,
                village = EXCLUDED.village,
                photo_url = EXCLUDED.photo_url,
                txn_date = EXCLUDED.txn_date,
                amount = EXCLUDED.amount,
                txn_no = EXCLUDED.txn_no
        """, (amb_id, name, phone, village, photo_url, txn_date, amount, txn_no))
        inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Inserted/updated: {inserted} (of which {unnamed} had no name on file), skipped (no AMB ID): {skipped}")


if __name__ == "__main__":
    main()
