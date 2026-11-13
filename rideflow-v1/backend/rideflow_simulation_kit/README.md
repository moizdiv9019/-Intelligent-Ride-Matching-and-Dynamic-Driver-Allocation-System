# RideFlow synthetic fleet simulation kit

This kit creates a **small, synthetic test dataset** in the single hosted Supabase project you are currently using. It reuses the existing Moiz passenger and Zameer driver accounts, then creates **7 additional drivers and 2 additional passengers**. The resulting demo dataset has 8 drivers and 3 passengers in total.

The scripts are intentionally non-destructive: they do not delete users, rides, offers, profiles, roles, or vehicles. They do not change schema. Run each setup step once. Generated accounts are confirmed test accounts using a shared password you enter locally; the password and Supabase secret key are never written to the manifest.

## Important safety notes

- This targets your **hosted project**, not a disposable local database. The generated users and vehicles are real rows in that project.
- The generator needs a Supabase **secret key** (`sb_secret_...`) or legacy `service_role` key. Admin user creation must run only from a trusted local/server environment. Never use this key in React, never commit it, and never paste it into chat. Supabase explicitly restricts Auth Admin user creation to trusted server-side code. See the official docs: https://supabase.com/docs/reference/python/auth-admin-createuser and https://supabase.com/docs/guides/getting-started/api-keys
- The generator asks for the key using hidden terminal input; it does not save or print it.
- Use a dedicated shared password for these disposable demo accounts. It is not stored in the manifest.
- The script expects the FastAPI server to be running at `http://127.0.0.1:8000` and uses your existing backend `.env` settings.
- The signup trigger must already be installed, and the driver onboarding endpoint must work (it has passed your earlier live test).
- Do not run the generated activation SQL until you've reviewed its exact user IDs.

## Dataset design

Existing accounts reused:

- `moiz9019455456@gmail.com` — passenger
- `zameer@gmail.com` — verified driver, refreshed to available with fresh GPS

Generated accounts:

- 7 driver users, onboarded through `POST /api/v1/drivers/me/onboarding`
- 2 passenger users, provisioned through Supabase Auth so your Auth trigger creates profiles and passenger roles

Generated driver scenarios after activation:

| Account | Intended state | Expected nearby search behavior |
|---|---|---|
| Existing Zameer | verified, available, fresh GPS | Eligible |
| Demo driver 01 | verified, available, fresh GPS | Eligible |
| Demo driver 02 | verified, available, fresh GPS | Eligible |
| Demo driver 03 | verified, available, fresh GPS | Eligible |
| Demo driver 04 | verified, available, fresh GPS | Eligible |
| Demo driver 05 | verified, available, 10-minute-old GPS | Excluded as stale |
| Demo driver 06 | verified, offline, fresh GPS | Excluded as offline |
| Demo driver 07 | pending, offline | Excluded as unverified |

This gives five eligible drivers under normal fresh-location conditions. A true `busy` scenario should be produced by accepting a real ride for a driver, not by manually editing `availability_status` to `busy` without an active ride.

## Installation and prerequisites

1. Extract the ZIP contents **inside `rideflow-v1/backend/`**. The archive places files under `rideflow_simulation_kit/` to avoid overwriting your existing backend README or code.
2. Open PowerShell in `rideflow-v1/backend`.
3. Activate your backend virtual environment if it is not already active.
4. Confirm `backend/.env` has `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, and the database settings your app already needs.
5. Keep the FastAPI server running in another terminal:

```powershell
python -m uvicorn app.main:app --reload
```

No additional Python package is needed beyond the dependencies already used by your backend (`httpx` and `pydantic-settings`).

## Step A — Create the synthetic users and vehicles

From `rideflow-v1/backend` run:

```powershell
python .\rideflow_simulation_kit\simulation\seed_fleet.py
```

The script will:

1. Require the exact confirmation string `CREATE-DEMO-DATA`.
2. Ask for your Supabase secret/service-role key without echoing it.
3. Ask you to choose and confirm a shared password of at least 8 characters for generated users.
4. Create 7 confirmed driver Auth users and 2 confirmed passenger Auth users.
5. Rely on your existing Auth trigger for each profile and default passenger role.
6. Sign in each generated driver with the publishable key and call your real driver onboarding endpoint to add their driver role, driver profile and vehicle.
7. Save a manifest and SQL files under `backend/simulation_data/run_<timestamp>_<suffix>/`.

It does not verify the generated drivers automatically. They start pending/offline as required by your current onboarding rules.

If the script stops partway through, inspect the partial manifest. Do not blindly rerun it; each run uses unique email addresses and therefore creates another set of accounts.

## Step B — Activate six generated drivers and seed one stale GPS row

Open the `activate_simulation.sql` file printed by Step A. Review that it references only the seven user IDs from the manifest. Execute it in Supabase SQL Editor.

It will:

- Mark generated drivers 01–06 as verified and initially offline.
- Leave driver 07 pending/offline.
- Set driver 05 to available but insert a deliberately stale GPS location (10 minutes old).
- Keep the other new drivers offline until the API activation script refreshes them.

The SQL intentionally does not create roles, profiles, users, vehicles, rides or offers.

## Step C — Refresh live driver GPS and availability through FastAPI

From `rideflow-v1/backend` run:

```powershell
python .\rideflow_simulation_kit\simulation\activate_and_locate.py
```

Enter the manifest path printed by Step A, the shared password from Step A, and Zameer's existing password. The script:

- Refreshes Zameer to available and sends fresh GPS.
- Sets generated drivers 01–04 available and sends fresh GPS at distinct Mysuru coordinates.
- Leaves driver 05 available without refreshing the deliberately stale GPS.
- Gives driver 06 a fresh GPS update through the API and then sets the driver offline.
- Leaves driver 07 pending/offline.

All online/availability/location changes go through your existing authenticated FastAPI endpoints.

After Step C, run the generated `set_idle_times.sql` in Supabase SQL Editor. It seeds different idle durations for the four eligible simulated drivers and Zameer so the idle-time matching factor can be observed. It only updates `last_became_available_at` for those exact test accounts.

## Step D — Inspect the resulting dataset

Run the `inspect_simulation.sql` file created alongside the manifest in Supabase SQL Editor. It returns status, vehicle, GPS timestamp, coordinates and location age for the exact seven generated drivers.

Expected broad shape:

- 4 generated verified/available/fresh drivers
- 1 generated verified/available/stale driver
- 1 generated verified/offline/fresh driver
- 1 generated pending/offline driver
- plus existing Zameer as an available/fresh eligible driver

A nearby search near the existing test pickup (`lat=12.2965`, `lng=76.6405`) should return Zameer and generated drivers 01–04, subject to your configured radius and exact location freshness threshold.

## Suggested simulation scenarios

Run these through your existing API scripts or Swagger UI. Each scenario that creates a ride writes real test records to the hosted project; use clear test addresses and retain the returned ride IDs.

1. **Normal allocation:** book a ride as Moiz and inspect `ride_offers.match_score` and `match_factors`.
2. **Ranking:** confirm the first offer goes to the highest-scoring eligible driver. Compare the stored factor values rather than assuming the closest straight-line point wins.
3. **Rejection:** reject the first offer and confirm the next eligible driver receives the next offer.
4. **Idle-time scoring:** inspect `last_became_available_at` and compare the stored `idle_score` in `match_factors` for candidate offers.
5. **Timeout:** let an offer expire and confirm the worker advances to another candidate. For manual testing, temporarily increase `OFFER_TIMEOUT_SECONDS` and restart FastAPI.
6. **Busy exclusion:** have one eligible driver accept a real offer, which should set that driver busy; then make another booking and confirm the busy driver is omitted.
7. **Stale GPS exclusion:** driver 05 should not appear in nearby results or dispatch candidates.
8. **Offline exclusion:** driver 06 should not appear as eligible.
9. **Pending verification exclusion:** driver 07 should not be able to go online or receive offers.
10. **Passenger cancellation:** create a fresh ride and cancel it as its passenger.
11. **Driver cancellation and redispatch:** have an eligible driver accept a fresh ride, cancel before journey start, and confirm the cancelling driver is not re-offered that same ride.
12. **No driver found:** use a controlled test window where all eligible test drivers are offline or otherwise ineligible; restore the seed statuses afterward.
13. **Multiple bookings:** issue two bookings close together and check that no driver is assigned more than one active ride.

## Cleanup

The scripts deliberately do not include an automatic cleanup command because the hosted project also contains your real test accounts and ride history. If you later want to delete this run, first inspect the manifest and run-specific ride IDs, then delete only the generated Auth users through the Supabase Dashboard or a carefully reviewed admin cleanup script. Deleting Auth users may cascade to linked profiles, roles and driver records depending on your foreign keys; check those relationships before cleanup. Do not delete the existing Moiz or Zameer accounts or the successful integration ride.

## Git safety

Add this to the repository `.gitignore` so local manifests and generated per-run SQL don't get committed:

```gitignore
backend/simulation_data/
```

The scripts themselves are safe to commit if your team wants to keep the simulation tooling. Never commit `.env`, passwords, tokens, or Supabase secret/service-role keys.
