# FLASH Registration App

A live, multi-desk registration app for Smaranotsavam 2026. Works on any phone/laptop browser — no app install needed. All desks share one database, so check-ins sync instantly across all of them.

## What it does

- **Desk page (`/`)**: search a contributor by phone, name, or AMB ID, and check them in with one tap. If they're not on the list, add them as a walk-in with just a name and phone.
- **Live view (`/dashboard`)**: auto-refreshing totals and a live feed of check-ins — open this on a projector or any organizer's phone.
- **Duplicate protection**: if two desks check in the same contributor at the same moment, only one succeeds — the second desk instantly sees "already checked in" with the time and which desk did it.

## 1. Deploy to Render (uses your existing `flash-db`)

1. Push this folder to a new GitHub repo (e.g. `flash-registration`).
2. In Render: **New → Web Service** → connect that repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add environment variable `DATABASE_URL` — use the **same Internal Database URL** from your existing `flash-db` (Render dashboard → flash-db → Connect → Internal Database URL). This app creates its own `contributors` and `checkins` tables in the same database, so it won't touch your existing WhatsApp tables.
6. Deploy. Render gives you a URL like `flash-registration.onrender.com`.

Since you're now on the paid `flash-db` plan, there's no spin-down/cold-start risk on the database side. If you deploy this as a **free** web service, the app itself may cold-start after inactivity — for event day, consider the paid web service tier too ($7/mo) so desks never hit a slow first load.

## 2. Load your contributor list

On your own computer (with Python installed):

```
pip install pandas openpyxl psycopg2-binary
set DATABASE_URL=<your External Database URL from Render>
python import_contributors.py path\to\master_list.xlsx
```

It auto-detects columns for AMB ID, Name, Phone, Village, and Photo URL from your Excel headers and shows you what it found before importing — confirm with `y`.

## 3. On event day

- Each desk opens `https://your-app.onrender.com/` on their phone/laptop, types their desk name once (e.g. "Desk 3 - Ramesh"), and starts searching + checking in.
- Put `https://your-app.onrender.com/dashboard` on the lobby screen or give organizers the link for their phones.

## Notes

- Family members arriving together: when checking in a contributor, the desk is prompted for "how many people including this contributor" — that count is added to the live total.
- Walk-ins are logged separately from contributors so you can see the split (dashboard shows both counts).
- All check-in timestamps are server time (IST, as set by Render).
