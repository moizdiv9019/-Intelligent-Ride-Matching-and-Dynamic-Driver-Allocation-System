Act as a senior React frontend engineer. We are building RideFlow, a real-time intelligent ride-hailing web application.

The FastAPI backend is already developed and connected to our hosted Supabase database. Live passenger booking, OSRM fare estimation, driver matching, driver offer retrieval and driver acceptance have been tested successfully.

Your task is to build the complete V1 frontend.

## TECHNOLOGY

- React
- Vite
- JavaScript (not TypeScript)
- React Router
- Supabase JavaScript SDK
- Leaflet
- React-Leaflet
- OpenStreetMap
- CSS

Do not use Next.js or TypeScript.

## PROJECT STRUCTURE

The root project contains:
- backend/
- frontend/
- supabase/
- docs/

Inspect the existing frontend directory before modifying it. Reuse existing code where appropriate. If it is empty, scaffold a React + Vite JavaScript application inside it.

## ENVIRONMENT

Create frontend/.env.example:

VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=

Never expose the Supabase service-role key.

## 1. AUTHENTICATION

Implement:
- Signup using Supabase Auth email/password.
- Login using Supabase Auth.
- Logout.
- Session persistence.
- Protected routes.
- Display user profile and email.
- Retrieve current user roles from GET /me.
- Route passengers and drivers to appropriate dashboards.

Signup should collect name, email and password. Pass full_name as Supabase user metadata so the existing database signup trigger can populate the profile.

Do not create roles directly from frontend code.

## 2. API CLIENT

Create a reusable API client.

Requirements:
- Read the current Supabase session using supabase.auth.getSession().
- Attach access_token as Authorization: Bearer token.
- Set Content-Type application/json where appropriate.
- Handle 401, 403, 409 and 500 errors.
- Show understandable error messages.
- Centralize API base URL configuration.

All business operations must go through FastAPI. Do not directly insert or update database tables using the frontend Supabase client.

## 3. PASSENGER DASHBOARD

Create an attractive responsive passenger dashboard.

Features:
- Leaflet interactive map using OpenStreetMap.
- Pickup location selection.
- Destination selection.
- Browser GPS to identify current pickup location.
- Display nearby available drivers.
- Display estimated journey distance.
- Display estimated duration.
- Display fare breakdown.
- Display route geometry as a map polyline.
- Request ride button.
- Show booking progress.
- Show accepted driver and vehicle information where permitted.
- Show current ride status.

Connect to:
GET /api/v1/drivers/nearby
POST /api/v1/rides/estimate
POST /api/v1/rides
GET /api/v1/rides/{ride_id}

After booking, poll the ride details every 3–5 seconds until the ride is accepted, cancelled or no_driver_found.

Keep client_request_id stable while retrying the same booking request.

## 4. DRIVER DASHBOARD

Create a separate driver dashboard.

Features:
- Driver profile and vehicle information.
- Online/offline toggle.
- Current availability status.
- Incoming ride offer card.
- Pickup location and approximate distance.
- Estimated pickup ETA.
- Estimated fare.
- Offer expiry countdown.
- Accept ride button.
- Reject ride button.
- Active ride details.
- Map showing pickup and destination.

Connect to:
GET /api/v1/drivers/me
PATCH /api/v1/drivers/me/availability
POST /api/v1/drivers/me/location
GET /api/v1/drivers/me/offers?status=offered
POST /api/v1/driver/offers/{offer_id}/accept
POST /api/v1/driver/offers/{offer_id}/reject
GET /api/v1/rides/{ride_id}

## 5. DRIVER GPS

Use browser navigator.geolocation.watchPosition().

Requirements:
- Request permission from the driver.
- Send GPS updates to FastAPI periodically, approximately every 5 seconds while the page is active.
- Include lat, lng, accuracy_m, heading_deg, speed_mps and device_recorded_at.
- Stop GPS watch when driver goes offline or logs out.
- Handle denied permissions and GPS errors.
- Show clear location status.

Do not claim background tracking works when the phone browser is closed or suspended.

## 6. MAP

Use React-Leaflet and OpenStreetMap tiles.

Display:
- Pickup marker.
- Destination marker.
- Passenger location.
- Approximate nearby driver locations.
- Route geometry from the backend.
- Driver location during an assigned active ride, when authorized.

Include OpenStreetMap attribution.

Remember GeoJSON coordinates are [longitude, latitude], whereas Leaflet positions are [latitude, longitude].

## 7. UI DESIGN

Build a polished, modern, responsive interface.

Style:
- Clean ride-hailing application design.
- Light background.
- Dark readable text.
- Attractive primary color.
- Clear buttons and status indicators.
- Mobile-first layout.
- Sidebar or compact navigation for desktop.
- Map and booking panel layout.

Create reusable components:
- Navbar
- Sidebar
- MapView
- RideEstimateCard
- DriverOfferCard
- RideStatusCard
- LoadingSpinner
- ErrorMessage

## 8. SECURITY

- Never expose service-role credentials.
- Do not trust frontend role values.
- Protect passenger and driver routes.
- Use backend authorization as the source of truth.
- Do not store access tokens manually; allow Supabase SDK to manage sessions.
- Do not expose exact locations of unrelated drivers.

## 9. TESTING

- Verify npm installation.
- Run npm run build.
- Fix compilation errors.
- Verify login, logout and route guards.
- Verify API client authorization header.
- Verify passenger and driver views.
- Verify map geometry conversion.

## 10. RESTRICTIONS

- Do not modify backend files.
- Do not modify Supabase schema.
- Do not invent API endpoints.
- Do not add payment functionality.
- Do not introduce Redux or unnecessary libraries.
- Do not create an admin dashboard in V1.
- Do not leave major screens as non-functional placeholders.

At completion, report the frontend structure, pages, components, APIs integrated, environment configuration and build results.