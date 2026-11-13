
Work ONLY inside the backend folder and the
supabase folder for database-related work.

Use:
Python, FastAPI, Supabase PostgreSQL,
PostGIS and Supabase JWT authentication.

Follow the existing API contract exactly.

Implement in this order:

1. FastAPI setup and health endpoint.
2. Environment configuration.
3. Supabase JWT verification.
4. Database connection.
5. GET /api/v1/me.
6. Driver availability and location.
7. Ride estimate and ride creation.
8. Driver offer retrieval.
9. Driver accept and reject.
10. Ride arrived, start and complete.
11. Sequential driver dispatch.
12. Fare calculation and error handling.

Implement and test one feature at a time.

Never trust user IDs submitted by the frontend.
Never expose service-role keys.
Do not change API response structures.