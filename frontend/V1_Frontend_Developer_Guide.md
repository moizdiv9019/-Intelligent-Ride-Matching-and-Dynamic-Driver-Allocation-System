# RideFlow V1 — Frontend Developer Guide

**Document:** Frontend implementation manual  
**Version:** 1.0  
**Project:** RideFlow — browser-based ride-hailing application  
**Audience:** Frontend developer (React experience), with beginner-friendly steps  
**Frontend stack:** React + Vite + JavaScript  
**Related document:** RideFlow V1 API Contract (20 endpoints)

---

## 1. What you are building

Build the browser application used by two types of users:

1. **Passenger:** creates an account, selects pickup and destination, views a fare estimate, books a ride, watches the ride status and assigned driver, cancels when allowed, and views ride history.
2. **Driver:** creates an account, submits vehicle information, waits for verification, goes online, shares GPS while the app is open, receives one ride offer at a time, accepts or rejects the offer, updates the ride as arrived/started/completed, and views ride history.

V1 is a **responsive web application**, not a native Android/iOS app. The driver should keep the browser page open in the foreground during the hackathon demo. Browser location access needs user permission and a secure context (HTTPS in deployment). Do not promise reliable GPS tracking when the browser is backgrounded or the phone is locked.

### V1 completion goal

A passenger and a verified driver, using two separate browser sessions or phones, can complete this flow:

1. Passenger signs in and selects a pickup and destination.
2. Passenger requests an estimate and sees the road distance, ETA, and estimated fare.
3. Passenger requests the ride.
4. The backend offers the ride to one eligible driver at a time.
5. The driver receives the offer and accepts it.
6. Passenger sees the accepted ride and permitted driver details/location.
7. Driver marks arrived, starts the journey, and completes it.
8. Passenger and driver can see the completed trip in history.

The backend is responsible for fare calculation, driver selection, offer expiry, ride-state validation, and permissions. The frontend displays the backend's result; it must not try to make those decisions itself.

---

## 2. Agreed architecture and tools

| Area | Use | Frontend responsibility |
|---|---|---|
| UI | React + Vite + JavaScript | Screens, forms, state, responsive layout |
| Navigation | React Router | Public, passenger, and driver routes |
| Authentication | Supabase Auth + Google OAuth | Sign up, sign in, sign out, keep session |
| API | FastAPI REST, `/api/v1` | Call endpoints with the Supabase access token |
| Realtime | Supabase Realtime | Listen for authorised changes, then refresh through REST |
| Database | Supabase PostgreSQL + PostGIS | Do not directly write ride business data from the browser |
| Map | Leaflet + React-Leaflet + OpenStreetMap | Show pickup, destination, route and permitted driver marker |
| Routing | Backend calls an OSRM-compatible provider | Display estimate/route returned by FastAPI |
| Hosting | Vercel for the Vite frontend | Build and publish the static frontend |

### Important architecture rule

**Supabase Auth is called directly from React. Ride business operations are called through FastAPI.** The frontend may use Supabase Realtime as a notification channel, but after receiving an event it should refresh the authorised resource through FastAPI.

Do not use the Supabase browser client to insert/update `rides`, `ride_offers`, `driver_locations`, or other business tables. Do not put a Supabase service-role key, database password, routing secret, or backend secret in React.

### Small dependencies to use

- `react-router-dom` — page navigation.
- `@supabase/supabase-js` — Supabase Auth and Realtime client.
- `leaflet` and `react-leaflet` — map UI.
- Native `fetch` — call FastAPI. Axios is not required for this V1.
- Plain CSS or CSS modules — styling. Avoid adding a large UI framework unless the team already knows it.
- `lucide-react` — optional icons only; it is not required for functionality.

---

## 3. Step 0 — Prepare your computer

Install these tools before coding:

1. **Node.js and npm.** Use a current Node LTS release supported by Vite. The current Vite guide lists Node.js 20.19+ or 22.12+ as the minimum supported lines; check the Vite guide if the toolchain has changed.
2. **VS Code** or another editor.
3. **Git** for version control.
4. **A GitHub account** and access to the team's repository.
5. **A modern browser** (Chrome is useful for testing browser permissions and the network console).

Check that the commands work:

```bash
node --version
npm --version
git --version
```

If `node` or `npm` is not recognised, install Node.js, close and reopen the terminal, and run the commands again.

---

## 4. Step 1 — Create the React + Vite project

Open a terminal in the parent folder where you keep your project, then run:

```bash
npm create vite@latest rideflow-frontend -- --template react
cd rideflow-frontend
npm install
npm run dev
```

Vite prints a local address, normally `http://localhost:5173`. Open it in the browser. You should see the default React/Vite page.

Stop the development server with `Ctrl + C` when you need to run another terminal command.

### Install the project packages

From inside `rideflow-frontend`, run:

```bash
npm install react-router-dom @supabase/supabase-js leaflet react-leaflet
```

Optional icons:

```bash
npm install lucide-react
```

Keep the `package-lock.json` file in Git. Both developers should use `npm install` so they install the dependencies recorded by the project.

---

## 5. Step 2 — Create the frontend folder structure

Create this structure under `src/` as the app grows. You do not need to create every file on day one; create a folder when you begin that feature.

```text
src/
  app/
    App.jsx
    router.jsx
  assets/
  components/
    common/
      AppHeader.jsx
      LoadingState.jsx
      ErrorMessage.jsx
      ConfirmDialog.jsx
    maps/
      RideMap.jsx
      MapMarker.jsx
      RouteLine.jsx
  context/
    AuthContext.jsx
  features/
    auth/
      LoginPage.jsx
      SignupPage.jsx
      AuthCallbackPage.jsx
    profile/
      ProfilePage.jsx
    passenger/
      PassengerHomePage.jsx
      RideRequestForm.jsx
      FareEstimateCard.jsx
      PassengerRidePage.jsx
      PassengerRideHistoryPage.jsx
    driver/
      DriverOnboardingPage.jsx
      DriverHomePage.jsx
      DriverOfferCard.jsx
      DriverActiveRidePage.jsx
      DriverRideHistoryPage.jsx
  hooks/
    useDriverLocation.js
    useRideRealtime.js
  lib/
    supabase.js
    api.js
    formatters.js
  services/
    authService.js
    rideService.js
    driverService.js
  styles/
    global.css
  main.jsx
```

**What belongs where:**

- `pages` (the `*Page.jsx` files) represent full screens.
- `components` are reusable pieces of UI.
- `services` contain feature-level API calls, such as requesting an estimate or accepting an offer.
- `lib/api.js` contains the shared HTTP request function.
- `context/AuthContext.jsx` makes the signed-in user/session available to pages.
- `hooks` hold reusable browser or realtime behaviour, such as GPS tracking.

Do not put all application code in `App.jsx`. Keep each screen and service small enough to understand and test.

---

## 6. Step 3 — Configure environment variables

Create a file named `.env.local` in the project root (the same folder as `package.json`):

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
VITE_SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
```

Get the Supabase project URL and **publishable** key from the Supabase dashboard. Do not use a `service_role` key here.

Create a safe example file named `.env.example` with placeholder values, so another developer knows which variables are required:

```env
VITE_API_BASE_URL=
VITE_SUPABASE_URL=
VITE_SUPABASE_PUBLISHABLE_KEY=
```

Check that `.env.local` is ignored by Git. If it is not, add it to `.gitignore` before committing.

**Important:** Vite exposes variables beginning with `VITE_` to browser code. Treat every `VITE_` value as public. Only put values there that are safe for the browser, such as the Supabase publishable key and public API base URL.

After editing environment variables, restart the Vite dev server.

---

## 7. Step 4 — Create the Supabase client

Create `src/lib/supabase.js`:

```javascript
import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabasePublishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY

if (!supabaseUrl || !supabasePublishableKey) {
  throw new Error('Missing Supabase environment variables. Check .env.local.')
}

export const supabase = createClient(supabaseUrl, supabasePublishableKey)
```

This one client is reused for sign-in, sign-out, reading the current session, and Realtime subscriptions.

---

## 8. Step 5 — Build authentication before the ride screens

Supabase Auth handles email/password authentication and Google OAuth. Do not create separate FastAPI login or signup endpoints; those are not part of the agreed API contract.

### 8.1 Configure Supabase dashboard

In the Supabase project:

1. Open **Authentication** and configure the providers you will support (email/password and Google).
2. For Google, create OAuth credentials in the Google Cloud Console and enter the client ID/secret in the Supabase provider settings. Keep the OAuth client secret in Supabase/provider configuration, never in React.
3. Set the local development site/redirect URLs, including `http://localhost:5173` and the callback route your app uses.
4. Later, add the deployed Vercel domain and its callback URL to the allowed redirect URLs.
5. Test email signup/login and Google login before working on ride requests.

### 8.2 Create the auth service

Create `src/services/authService.js`:

```javascript
import { supabase } from '../lib/supabase'

export async function signUpWithEmail(email, password) {
  return supabase.auth.signUp({ email, password })
}

export async function signInWithEmail(email, password) {
  return supabase.auth.signInWithPassword({ email, password })
}

export async function signInWithGoogle() {
  return supabase.auth.signInWithOAuth({
    provider: 'google',
    options: {
      redirectTo: `${window.location.origin}/auth/callback`,
    },
  })
}

export async function signOut() {
  return supabase.auth.signOut()
}
```

Create the login and signup forms. Show a useful message when credentials are invalid, email confirmation is required, or Google sign-in fails.

### 8.3 Keep track of the session

Create `AuthContext.jsx` to:

1. Call `supabase.auth.getSession()` when the app starts.
2. Subscribe to `supabase.auth.onAuthStateChange(...)`.
3. Store the current session and user in React state.
4. Unsubscribe from the auth listener when the provider unmounts.
5. Expose `session`, `user`, and `loading` to the rest of the app.

Do not assume the user is signed out while the first session check is still loading. Show a loading screen until the check finishes.

A minimal starting implementation for `src/context/AuthContext.jsx` is:

```jsx
import { createContext, useContext, useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'
import { getMyProfile } from '../services/rideService'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  const [profile, setProfile] = useState(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [profileLoading, setProfileLoading] = useState(false)

  useEffect(() => {
    let mounted = true

    supabase.auth.getSession().then(({ data, error }) => {
      if (!mounted) return
      if (error) console.error('Session check failed:', error.message)
      setSession(data?.session ?? null)
      setAuthLoading(false)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      setAuthLoading(false)
    })

    return () => {
      mounted = false
      listener.subscription.unsubscribe()
    }
  }, [])

  useEffect(() => {
    let active = true

    if (!session?.access_token) {
      setProfile(null)
      setProfileLoading(false)
      return
    }

    setProfileLoading(true)
    getMyProfile()
      .then((result) => { if (active) setProfile(result) })
      .catch((error) => {
        if (active) {
          setProfile(null)
          console.error('Unable to load application profile:', error.code || error.message)
        }
      })
      .finally(() => { if (active) setProfileLoading(false) })

    return () => { active = false }
  }, [session?.access_token])

  const value = {
    session,
    user: session?.user ?? null,
    profile,
    authLoading,
    profileLoading,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
```

In the app entry point (`src/main.jsx`), wrap the app in `BrowserRouter` and `AuthProvider`:

```jsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import App from './app/App'
import './styles/global.css'
import 'leaflet/dist/leaflet.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
)
```

The `getMyProfile` service function shown later should be exported from `rideService.js`. Show a loading state until both auth and the application profile check have completed. If the profile request fails, provide a retry path rather than silently treating the user as a passenger.

### 8.4 After login, retrieve the user's application profile

Once Supabase has authenticated the user, call:

```http
GET /api/v1/me
Authorization: Bearer <Supabase access token>
```

Use the returned `roles` to decide which part of the application to show. A person can have more than one role, so do not hard-code the assumption that every account is only a passenger or only a driver.

If a new user needs a profile, show a profile form and call `PUT /api/v1/me/profile`. The backend derives the identity from the token; do not send a user ID and trust it as proof of identity.

### 8.5 Protect pages

Create route protection so that:

- Signed-out users are sent to `/login`.
- Authenticated passengers can open passenger pages.
- Authenticated drivers can open driver pages.
- A passenger cannot open a driver's offer/ride controls merely by typing the URL.
- A pending/unverified driver sees a clear verification-pending screen and cannot go online.

The frontend route guard improves the UI, but it is not a security boundary. FastAPI must still verify the role and permission for every request.

---

## 9. Step 6 — Set up application routes and shared UI

Suggested routes:

| Route | Who can open it | Purpose |
|---|---|---|
| `/` | Everyone | Landing page / redirect |
| `/login` | Signed out | Login |
| `/signup` | Signed out | Registration |
| `/auth/callback` | Auth flow | Finish OAuth return |
| `/profile` | Authenticated | Complete or edit profile |
| `/passenger` | Passenger | Passenger home and map |
| `/passenger/request` | Passenger | Pickup, destination, estimate and booking |
| `/passenger/rides/:rideId` | Booking passenger | Current ride status and tracking |
| `/passenger/history` | Passenger | Past rides |
| `/driver/onboarding` | Authenticated user | Driver and vehicle details |
| `/driver` | Driver | Availability, map and incoming offers |
| `/driver/rides/:rideId` | Assigned driver | Active ride controls |
| `/driver/history` | Driver | Past rides |

Build a shared header with the user's name, current role/context, and sign-out control. Build shared loading, empty, error, and confirmation components. Every screen that loads data should show:

- loading state;
- success state;
- empty state, when there is no data;
- error state with a retry action.

Do not leave a blank screen when an API call fails.

---

## 10. Step 7 — Build the shared FastAPI request helper

Every protected FastAPI endpoint expects the Supabase access token in the `Authorization` header. Create one common request helper so every feature does not reimplement token handling.

Create `src/lib/api.js`:

```javascript
import { supabase } from './supabase'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

export async function apiRequest(path, options = {}) {
  if (!API_BASE_URL) {
    throw new Error('Missing VITE_API_BASE_URL in .env.local')
  }

  const { data, error: sessionError } = await supabase.auth.getSession()
  if (sessionError) throw sessionError

  const accessToken = data.session?.access_token
  if (!accessToken) {
    throw new Error('Please sign in to continue.')
  }

  const method = options.method || 'GET'
  const headers = {
    Accept: 'application/json',
    Authorization: `Bearer ${accessToken}`,
    ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...options.headers,
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  })

  const payload = await response.json().catch(() => ({}))

  if (!response.ok) {
    const apiError = payload.error || {}
    const error = new Error(apiError.message || `Request failed (${response.status})`)
    error.status = response.status
    error.code = apiError.code || 'UNKNOWN_ERROR'
    error.details = apiError.details || {}
    error.requestId = apiError.request_id || null
    throw error
  }

  return payload.data
}
```

This helper expects the agreed response envelope: successful responses use `{ "data": ... }`; failures use `{ "error": { "code", "message", "details", "request_id" } }`.

### Example API service functions

Create service functions instead of putting `fetch()` directly inside every button handler:

```javascript
// src/services/rideService.js
import { apiRequest } from '../lib/api'

export function getMyProfile() {
  return apiRequest('/me')
}

export function updateMyProfile(body) {
  return apiRequest('/me/profile', { method: 'PUT', body })
}

export function estimateRide(body) {
  return apiRequest('/rides/estimate', { method: 'POST', body })
}

export function createRide(body) {
  return apiRequest('/rides', { method: 'POST', body })
}

export function getRide(rideId) {
  return apiRequest(`/rides/${encodeURIComponent(rideId)}`)
}
```

Add similar service functions for driver onboarding, location, availability, offers, ride status, cancellation and history.

### Handle errors by code, not only by HTTP status

At minimum, display user-friendly messages for:

| Code | Frontend behaviour |
|---|---|
| `VALIDATION_ERROR` | Show field/form validation details |
| `UNAUTHORIZED` | Ask the user to sign in again |
| `FORBIDDEN` | Show an access/verification message |
| `NOT_FOUND` | Show that the ride/offer is unavailable |
| `RIDE_STATE_CONFLICT` | Refresh the ride; explain it changed elsewhere |
| `OFFER_EXPIRED` | Remove the expired offer and refresh offers |
| `DRIVER_UNAVAILABLE` | Tell driver the offer is no longer available |
| `RATE_LIMITED` | Avoid rapid retries; show a wait message |
| `ROUTING_ERROR` | Ask user to retry the estimate |
| `SERVICE_UNAVAILABLE` | Show temporary service issue and retry option |

Never show raw stack traces to the user. Keep `request_id` available in developer diagnostics so the backend developer can find the matching server log.

---

## 11. Step 8 — Build the map component

### 11.1 Import Leaflet CSS

In `src/main.jsx` (or once in a global stylesheet entry), import:

```javascript
import 'leaflet/dist/leaflet.css'
```

### 11.2 Create a basic reusable map

Create `src/components/maps/RideMap.jsx`:

```jsx
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

export default function RideMap({ center, pickup, destination, driver }) {
  return (
    <div className="ride-map">
      <MapContainer
        center={[center.lat, center.lng]}
        zoom={13}
        scrollWheelZoom
        style={{ height: '100%', width: '100%' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {pickup && (
          <Marker position={[pickup.lat, pickup.lng]}>
            <Popup>Pickup</Popup>
          </Marker>
        )}

        {destination && (
          <Marker position={[destination.lat, destination.lng]}>
            <Popup>Destination</Popup>
          </Marker>
        )}

        {driver && (
          <Marker position={[driver.lat, driver.lng]}>
            <Popup>Assigned driver</Popup>
          </Marker>
        )}
      </MapContainer>
    </div>
  )
}
```

Add a defined height in CSS. For example:

```css
.ride-map {
  width: 100%;
  height: min(65vh, 620px);
  min-height: 320px;
}
```

A map with no defined height may appear blank. Make sure the Leaflet CSS is loaded and the map container has a height.

### 11.3 Display the route returned by the estimate API

The API contract returns a GeoJSON-style `LineString`. Its coordinates are `[longitude, latitude]`. Leaflet markers and polylines expect `[latitude, longitude]`.

Convert the coordinates before passing them to `Polyline`:

```javascript
const routePositions = estimate.route.coordinates.map(
  ([longitude, latitude]) => [latitude, longitude]
)
```

Then render:

```jsx
import { Polyline } from 'react-leaflet'

{routePositions.length > 1 && <Polyline positions={routePositions} />}
```

Do not reverse the coordinates in the backend request. The API's point objects use `{ lat, lng }`; GeoJSON LineString arrays use `[lng, lat]`.

To let a user select a point by clicking the map, add a small child component that uses Leaflet's map events:

```jsx
import { useMapEvents } from 'react-leaflet'

function MapClickHandler({ onSelect }) {
  useMapEvents({
    click(event) {
      onSelect({
        lat: event.latlng.lat,
        lng: event.latlng.lng,
      })
    },
  })
  return null
}
```

Render `<MapClickHandler onSelect={setPickup} />` or `<MapClickHandler onSelect={setDestination} />` inside `MapContainer`. Keep a clear UI control showing whether the next map click sets pickup or destination. Store the selected point in parent React state and pass it to the map as a marker prop.

### 11.4 Important V1 address-search limitation

The agreed 20-endpoint API contract does **not** include an address-autocomplete or geocoding endpoint. Therefore, implement V1 pickup/destination selection using:

- a map tap/click to choose the coordinate; and
- a text field where the user enters a readable place label/address.

Do not invent a geocoding endpoint or call a secret provider key from the browser. If proper address search is required, the team must select a geocoding provider and add that integration to the architecture/API contract separately.

### 11.5 Map tile usage

Always show OpenStreetMap attribution. The public OpenStreetMap tile service is best-effort and has a usage policy; it is suitable for a small hackathon demo only when its policy is followed. For a public launch or higher traffic, choose a tile provider with an appropriate service agreement or host tiles yourself. Do not bulk-download or prefetch public OSM tiles.

---

## 12. Step 9 — Build the passenger journey

Build and test each stage before connecting the next one.

### 12.1 Passenger home

Show:

- map;
- pickup selector;
- destination selector;
- vehicle type selector (vehicle types supported by the backend);
- button to get estimate;
- button to request ride after an estimate has succeeded.

For pickup's initial value, you can offer a **Use my current location** button using `navigator.geolocation.getCurrentPosition()`. Ask permission only after the user taps that button. If denied or unavailable, let the passenger select the point on the map.

### 12.2 Get estimate

Call:

```http
POST /api/v1/rides/estimate
```

Example body:

```json
{
  "pickup": {
    "lat": 16.726,
    "lng": 74.497,
    "address": "Athani Bus Stand"
  },
  "destination": {
    "lat": 16.852,
    "lng": 74.581,
    "address": "Belagavi Main Bus Stand"
  },
  "vehicle_type": "car"
}
```

Display the returned `distance_m`, `duration_s`, `estimated_fare`, and `currency`. Convert metres to kilometres and seconds to minutes for display; keep the original values for API/state work. Draw the returned route on the map.

The backend calculates the road route and estimate. Do not calculate the authoritative fare in React.

### 12.3 Request the ride

Call:

```http
POST /api/v1/rides
```

Example body:

```json
{
  "client_request_id": "GENERATE-ONE-UUID-FOR-THIS-BOOKING",
  "pickup": {
    "lat": 16.726,
    "lng": 74.497,
    "address": "Athani Bus Stand"
  },
  "destination": {
    "lat": 16.852,
    "lng": 74.581,
    "address": "Belagavi Main Bus Stand"
  },
  "vehicle_type": "car"
}
```

Generate the idempotency ID once for a booking attempt:

```javascript
const clientRequestId = crypto.randomUUID()
```

Keep that same ID if the network request times out and the user retries the **same** booking. Do not generate a new ID for every retry, or the backend cannot recognise the retry as the same logical request. Generate a new ID only when the passenger intentionally starts a new booking.

After the API succeeds, save the returned ride ID in React state and navigate to `/passenger/rides/:rideId`. The backend will recalculate the route and fare and begin sequential driver dispatch. The frontend must not pick a driver.

### 12.4 Waiting for a driver

Show the ride as searching/requested while the backend dispatches. Show a waiting message and a cancel action if the current ride state permits cancellation.

Subscribe to the passenger's authorised ride change in Supabase Realtime. When a relevant event arrives, call `GET /api/v1/rides/{ride_id}` and refresh the screen from the REST response. If the realtime connection drops, show a connection indicator and provide a manual refresh option.

Possible results include:

- `accepted` — a driver accepted;
- `no_driver_found` — the backend exhausted eligible candidates;
- `cancelled` — the ride was cancelled.

Do not keep showing an infinite spinner if `no_driver_found` or `cancelled` is returned.

### 12.5 Active ride screen

For an accepted ride, display the permitted assigned-driver details and vehicle information returned by the API. During an authorised active ride, listen for the assigned driver's permitted location updates and move the driver marker on the map.

Do not query or display exact locations for unrelated drivers. The `GET /api/v1/drivers/nearby` endpoint returns approximate locations only.

### 12.6 Cancel passenger ride

Call:

```http
POST /api/v1/rides/{ride_id}/cancel
```

Body:

```json
{ "reason": "changed_my_mind" }
```

Show a confirmation before submitting. Refresh the ride after the response. The backend validates whether cancellation is allowed in the current state.

### 12.7 Passenger history

Call:

```http
GET /api/v1/me/rides?role=passenger&limit=10
```

Use `pagination.next_cursor` to load more history if needed. Show pickup, destination, status, date and final fare when available.

---

## 13. Step 10 — Build the driver journey

Test the driver flow in a separate browser profile or a second phone, not in the same signed-in passenger session.

### 13.1 Driver onboarding

Collect the vehicle information and call:

```http
POST /api/v1/drivers/me/onboarding
```

Example body:

```json
{
  "vehicle": {
    "vehicle_type": "car",
    "make_model": "Maruti Swift",
    "registration_number": "KA01AB1234",
    "seat_capacity": 4,
    "wheelchair_accessible": false
  }
}
```

Show the verification status returned by the backend. A newly onboarded driver is expected to be pending and offline until verified. Do not show an enabled Go Online control until the backend reports the driver as verified.

### 13.2 Driver home and online/offline control

Call `GET /api/v1/drivers/me` to retrieve the driver's current profile, vehicle and availability status.

When the verified driver taps **Go Online**, request browser geolocation permission and start tracking. Then call:

```http
PATCH /api/v1/drivers/me/availability
```

Body:

```json
{ "availability_status": "available" }
```

When the driver taps **Go Offline**, stop GPS tracking and request `availability_status: "offline"` from the backend. The backend owns `busy`; the frontend must not set a driver to busy by itself.

### 13.3 Create the GPS tracking hook

Use `navigator.geolocation.watchPosition()` to receive location updates. It requires the user's permission and a secure context; production must use HTTPS. Always call `clearWatch()` when tracking stops or the component is cleaned up.

Example hook/service logic:

```javascript
import { useEffect, useRef, useState } from 'react'
import { apiRequest } from '../lib/api'

export function useDriverLocation(isTracking) {
  const watchIdRef = useRef(null)
  const [locationError, setLocationError] = useState('')

  useEffect(() => {
    if (!isTracking) return

    if (!navigator.geolocation) {
      setLocationError('This browser does not support location.')
      return
    }

    let lastSentAt = 0

    watchIdRef.current = navigator.geolocation.watchPosition(
      async (position) => {
        const coords = position.coords
        setLocationError('')

        // Starter throttle only. Agree the final interval/rate limit with the backend developer.
        if (Date.now() - lastSentAt < 5000) return
        lastSentAt = Date.now()

        try {
          await apiRequest('/drivers/me/location', {
            method: 'POST',
            body: {
              lat: coords.latitude,
              lng: coords.longitude,
              accuracy_m: coords.accuracy,
              heading_deg: coords.heading,
              speed_mps: coords.speed,
              recorded_at: new Date(position.timestamp).toISOString(),
            },
          })
        } catch (error) {
          console.error('Unable to send driver location:', error.code || error.message)
        }
      },
      (error) => setLocationError(error.message),
      {
        enableHighAccuracy: true,
        maximumAge: 5000,
        timeout: 15000,
      }
    )

    return () => {
      if (watchIdRef.current !== null) {
        navigator.geolocation.clearWatch(watchIdRef.current)
        watchIdRef.current = null
      }
    }
  }, [isTracking])

  return { locationError }
}
```

This is a starting implementation. Coordinate with the backend developer on which GPS fields may be `null`, how frequently location updates should be accepted, and how to handle rate limits. Do not send a new location row to the database directly; the API updates the driver's latest-location record.

Show a clear message if permission is denied, location times out, or GPS is unavailable. Do not pretend the driver is tracking if requests are failing. For V1, ask the driver to keep the browser open and the screen active during the demo.

### 13.4 Receive offers

Subscribe to the driver's authorised `ride_offers` changes (filtered to the authenticated driver's own ID and protected by Realtime RLS). When an event arrives, call:

```http
GET /api/v1/drivers/me/offers?status=offered
```

Render the offer as a card showing pickup, destination, pickup distance, pickup ETA, estimated fare and expiry time. Do not assume the event payload contains everything the screen needs; fetch the current offer through FastAPI.

### 13.5 Accept an offer

Call:

```http
POST /api/v1/driver/offers/{offer_id}/accept
```

There is no request body. Disable the Accept button while the request is in progress. If the backend reports `OFFER_EXPIRED`, `DRIVER_UNAVAILABLE`, or `RIDE_STATE_CONFLICT`, refresh the offers and show the correct updated state.

### 13.6 Reject an offer

Call:

```http
POST /api/v1/driver/offers/{offer_id}/reject
```

Example body:

```json
{ "reason": "too_far" }
```

Supported reasons in the API contract are `too_far`, `busy`, `personal_reason`, `vehicle_issue`, and `other`. After rejection, refresh the offers. The backend decides which driver receives the next offer.

### 13.7 Update the ride lifecycle

After acceptance, use the active ride screen and show only the action valid for the current state:

| Driver action | API call | Expected resulting status |
|---|---|---|
| Mark arrived | `POST /api/v1/driver/rides/{ride_id}/arrived` | `arriving` |
| Start journey | `POST /api/v1/driver/rides/{ride_id}/start` | `ongoing` |
| Complete journey | `POST /api/v1/driver/rides/{ride_id}/complete` | `completed` |
| Cancel before journey starts | `POST /api/v1/driver/rides/{ride_id}/cancel` | Ride returns to dispatch/searching flow |

Refresh the ride from `GET /api/v1/rides/{ride_id}` after each action. Do not let the frontend jump ahead in the lifecycle or display controls that do not fit the current state.

The driver may cancel before the journey starts according to the contract. Do not silently reassign an in-progress passenger after the ride has started.

The backend calculates the final fare. The driver UI must not submit or edit the final fare.

### 13.8 Driver history

Call:

```http
GET /api/v1/me/rides?role=driver&limit=10
```

Use the returned pagination fields for additional pages.

---

## 14. Step 11 — Add Supabase Realtime subscriptions

V1 uses Supabase Realtime Postgres Changes as the simple first implementation, subject to the RLS rules configured by the backend developer.

### What each user listens to

| User | Table | Filter/meaning |
|---|---|---|
| Driver | `ride_offers` | Only offers belonging to that authenticated driver |
| Passenger | `rides` | Only the passenger's own ride / authorised ride ID |
| Passenger on active ride | `driver_locations` | Only the assigned driver's permitted live location |

### Example: passenger ride subscription

Create a hook such as `src/hooks/useRideRealtime.js`. The `refreshRide` function should be stable (wrap it in `useCallback` in the page component) so the subscription is not recreated on every render.

```javascript
import { useEffect } from 'react'
import { supabase } from '../lib/supabase'

export function useRideRealtime(rideId, refreshRide) {
  useEffect(() => {
    if (!rideId) return

    const channel = supabase
      .channel(`ride-${rideId}`)
      .on(
        'postgres_changes',
        {
          event: 'UPDATE',
          schema: 'public',
          table: 'rides',
          filter: `id=eq.${rideId}`,
        },
        () => {
          // Notification only: refresh through the authenticated FastAPI endpoint.
          refreshRide()
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [rideId, refreshRide])
}
```

Use an equivalent hook for driver offers, with the filter set to the authenticated driver's own `driver_user_id`. The backend developer must confirm the exact column names and RLS policies before integration.

### Realtime rules

1. Subscribe only after the user is authenticated and the app knows which resource they are allowed to see.
2. Do not trust a client-side filter as the only security control. RLS/policies must restrict the rows the user can receive.
3. Remove subscriptions when leaving the screen or changing ride IDs. Do not create duplicate subscriptions after every render.
4. Treat events as notifications, not as authoritative business results.
5. After a notification, call FastAPI to refresh the ride, offer, or location resource.
6. Realtime must not be used to bypass the REST API for accept/reject/cancel/start/complete actions.

Supabase's current documentation recommends Broadcast for scalability and security, while Postgres Changes is simpler but less scalable. The agreed V1 contract uses Postgres Changes for the initial low-volume hackathon build. Keep the event-handling code isolated so the team can move to Broadcast later if required.

---

## 15. Step 12 — Use the 20 API endpoints in the frontend

All paths below are relative to the base URL configured in `VITE_API_BASE_URL`, which already ends in `/api/v1`. All listed FastAPI calls require the Supabase access token.

| # | Method | Path after `/api/v1` | Frontend use |
|---:|---|---|---|
| 1 | PUT | `/me/profile` | Save profile details |
| 2 | GET | `/me` | Load current profile and roles |
| 3 | POST | `/drivers/me/onboarding` | Submit driver and vehicle details |
| 4 | GET | `/drivers/me` | Load driver profile, vehicle and status |
| 5 | PATCH | `/drivers/me/availability` | Request available/offline |
| 6 | POST | `/drivers/me/location` | Send driver's latest GPS update |
| 7 | GET | `/drivers/nearby?lat=&lng=&radius_m=` | Show approximate nearby drivers when useful |
| 8 | POST | `/rides/estimate` | Calculate estimate and route |
| 9 | POST | `/rides` | Create booking and start dispatch |
| 10 | GET | `/drivers/me/offers?status=offered` | Load current driver's offers |
| 11 | POST | `/driver/offers/{offer_id}/accept` | Accept offer |
| 12 | POST | `/driver/offers/{offer_id}/reject` | Reject offer and reason |
| 13 | GET | `/rides/{ride_id}` | Refresh ride details/status |
| 14 | GET | `/me/rides?role=passenger\|driver&limit=10` | Load ride history |
| 15 | POST | `/driver/rides/{ride_id}/arrived` | Mark arrival at pickup |
| 16 | POST | `/driver/rides/{ride_id}/start` | Start journey |
| 17 | POST | `/driver/rides/{ride_id}/complete` | Complete journey; backend sets fare |
| 18 | POST | `/rides/{ride_id}/cancel` | Passenger cancellation |
| 19 | POST | `/driver/rides/{ride_id}/cancel` | Driver cancellation before start |
| 20 | GET | `/rides/{ride_id}/events` | Show ride event timeline |

For exact request/response JSON examples, follow the separate **RideFlow V1 API Contract**. Do not make a different field name or response shape in the frontend without updating the shared contract and coordinating with the backend developer.

---

## 16. Step 13 — Manage frontend state carefully

Use React state for the information needed by the visible screen. For V1, React Context plus local component state is enough; a large state-management library is unnecessary.

Suggested state groups:

- **Auth state:** session, user, roles, profile loading.
- **Passenger booking state:** pickup, destination, vehicle type, estimate, current ride ID.
- **Passenger active ride state:** latest ride, events, assigned driver location, realtime connection status.
- **Driver state:** driver profile, verification status, availability, GPS tracking status, active offers, active ride.

Rules:

1. The API response is the source of truth for ride state and price.
2. Do not store a derived fare as the only source of the amount; keep the backend response.
3. Do not set a ride to `accepted` locally just because the driver pressed Accept. Wait for the API response and refresh.
4. Do not create duplicate booking requests from a double click. Disable the action while it is submitting and use the same `client_request_id` when retrying the same booking.
5. Clear a user's active screen data when they sign out.
6. Handle browser refresh: re-read the URL's `rideId`, then fetch the ride from FastAPI instead of depending only on in-memory state.

---

## 17. Step 14 — Responsive design and accessibility

The application must work on a desktop/laptop and a mobile browser because the driver is expected to use a phone during the demo.

Minimum UI requirements:

- Large touch targets for driver Accept, Reject, Arrived, Start and Complete actions.
- Map and action panel fit on a mobile screen without horizontal scrolling.
- Text and status colours remain readable in daylight.
- Forms have visible labels, validation messages and keyboard support.
- Buttons show disabled/loading state while a request is in progress.
- Confirm before cancellation or other destructive actions.
- The user can understand whether they are offline, online, searching, on a ride, or disconnected.
- Provide a useful message when location permission is denied or GPS is unavailable.

Do not build animations or decorative elements before the core ride flow works.

---

## 18. Step 15 — Test locally before deployment

### 18.1 Start the services

For local end-to-end testing, the backend developer must have FastAPI running and provide the base URL. For example:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Use the real local URL/port supplied by the backend developer. Confirm the API responds before debugging React.

### 18.2 Test with two accounts

Use two separate browser profiles, incognito sessions, or a passenger phone and a driver phone:

- Account A: passenger.
- Account B: driver (verified by the backend/admin test setup).

### 18.3 Manual test checklist

**Authentication**

- [ ] Sign up with email.
- [ ] Sign in with email.
- [ ] Sign in with Google.
- [ ] Refresh a signed-in page; session is restored.
- [ ] Sign out; protected pages are blocked.
- [ ] A user without the driver role cannot open driver controls.

**Passenger**

- [ ] Select pickup and destination on the map.
- [ ] Enter readable place labels.
- [ ] Estimate returns distance, ETA, fare and route.
- [ ] Invalid points show a validation error.
- [ ] One booking creates one ride.
- [ ] Double-click/retry does not create duplicate rides.
- [ ] Searching state is shown while dispatch runs.
- [ ] Driver acceptance updates the passenger screen.
- [ ] Driver location appears only for the assigned active ride.
- [ ] Passenger cancellation updates the screen.
- [ ] No-driver-found state is understandable.
- [ ] Ride history loads.

**Driver**

- [ ] Submit onboarding details.
- [ ] Pending driver cannot go online.
- [ ] Verified driver can request online status.
- [ ] Browser requests location permission after Go Online.
- [ ] Location updates reach FastAPI.
- [ ] Offer appears and can be accepted.
- [ ] Offer can be rejected with a valid reason.
- [ ] Expired offer is removed/refreshed.
- [ ] Arrived, Start and Complete actions work in order.
- [ ] Final fare comes from the backend response.
- [ ] Driver can go offline and location tracking stops.
- [ ] Ride history loads.

**Realtime and reliability**

- [ ] Realtime event refreshes the correct resource through REST.
- [ ] Leaving a ride screen unsubscribes its channel.
- [ ] A realtime disconnect does not leave the user with an endless spinner.
- [ ] Network/API errors display helpful messages.
- [ ] Browser refresh during an active ride loads the ride again from its ID.

**Mobile and production readiness**

- [ ] Test on a real mobile browser over HTTPS.
- [ ] Test location denial and location timeout.
- [ ] Keep the driver's browser foregrounded for the demo.
- [ ] `npm run build` finishes without errors.
- [ ] Browser console has no uncaught errors.

### 18.4 Build check

Before every major merge or deployment:

```bash
npm run build
```

Fix build errors before pushing. Also test the production build locally with:

```bash
npm run preview
```

---

## 19. Step 16 — Deploy the frontend to Vercel

This guide uses Vercel as the straightforward V1 static-hosting choice. The FastAPI backend is deployed separately; this frontend deployment does not deploy the backend.

### 19.1 Push the frontend to GitHub

From the frontend project root:

```bash
git init
git add .
git commit -m "Create RideFlow frontend"
git branch -M main
git remote add origin https://github.com/YOUR_ORG/rideflow-frontend.git
git push -u origin main
```

If the team has already created a repository, use its existing remote URL rather than running `git remote add origin` again.

### 19.2 Create a Vercel project

1. Sign in to Vercel.
2. Select **Add New Project** and import the frontend GitHub repository.
3. If the frontend is in its own repository, the root directory is the repository root. If the repository contains both frontend and backend folders, set the Vercel Root Directory to the frontend folder.
4. Framework preset: Vite (usually detected automatically).
5. Build command: `npm run build`.
6. Output directory: `dist`.
7. Add the environment variables below before deploying.

### 19.3 Set production environment variables

In Vercel Project Settings → Environment Variables, configure:

```env
VITE_API_BASE_URL=https://YOUR-DEPLOYED-API-DOMAIN/api/v1
VITE_SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
```

Use the real HTTPS backend URL once the backend developer has deployed FastAPI. Do not deploy the frontend with `127.0.0.1` or `localhost` as its API URL; that points to the visitor's own device, not your backend server.

### 19.4 Configure single-page application routing

If direct navigation/refresh on routes such as `/passenger/rides/ride-id` returns a 404, add `vercel.json` at the frontend project root:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

This sends browser routes back to the Vite app so React Router can handle them.

### 19.5 Update Supabase OAuth redirect configuration

After Vercel assigns the project domain:

1. Open Supabase Authentication URL configuration.
2. Set the production Site URL.
3. Add the exact production callback URL, for example `https://YOUR-APP.vercel.app/auth/callback`, to the allowed redirect URLs.
4. Keep localhost URLs for development.
5. Test Google OAuth from the deployed app, not only locally.

### 19.6 Coordinate backend CORS

Give the backend developer the exact deployed frontend origin, for example:

```text
https://YOUR-APP.vercel.app
```

The backend must allow that origin in its CORS configuration. The frontend cannot fix a backend CORS rejection.

### 19.7 Verify the deployed site

After deployment:

1. Open the Vercel URL on desktop and mobile.
2. Sign in and sign out.
3. Check `GET /me` succeeds against the deployed FastAPI URL.
4. Test the passenger estimate and booking.
5. Test driver GPS on HTTPS with permission granted.
6. Test a complete ride using two accounts.
7. Refresh an active route directly to verify SPA rewrites.
8. Check browser DevTools → Network and Console for failed API requests, CORS errors and uncaught exceptions.

Every time the backend's production URL changes, update `VITE_API_BASE_URL` in Vercel and redeploy.

---

## 20. Step 17 — Frontend security checklist

- [ ] Only the Supabase publishable key is used in the browser.
- [ ] No service-role key, database password, routing provider secret or private API key is committed.
- [ ] Protected FastAPI requests include `Authorization: Bearer <access_token>`.
- [ ] UI role checks are not treated as backend authorization.
- [ ] No user ID supplied by the browser is treated as proof of identity.
- [ ] Exact driver GPS is not shown to unrelated passengers.
- [ ] Realtime subscriptions are protected by RLS and cleaned up.
- [ ] Passenger and driver actions go through FastAPI.
- [ ] Fare shown to users is returned by the backend.
- [ ] Production uses HTTPS.
- [ ] `.env.local` is not committed.

---

## 21. Recommended frontend work order

Follow this order instead of building all screens at once:

| Phase | Work | Completion proof |
|---:|---|---|
| 1 | Create Vite project, folders, CSS, GitHub repo | Default app runs and pushes to GitHub |
| 2 | Supabase client, signup/login, Google OAuth, AuthContext | User can sign in, refresh and sign out |
| 3 | API helper, `/me`, profile form, route protection | Authenticated user profile and roles load |
| 4 | Leaflet map and map point selection | Pickup/destination markers work on desktop/mobile |
| 5 | Passenger estimate and booking | Estimate and one ride request reach FastAPI |
| 6 | Driver onboarding, status and GPS | Verified driver can go online and post location |
| 7 | Driver offers, accept/reject | Driver receives and responds to offer |
| 8 | Ride status pages and Realtime | Passenger and driver see the ride lifecycle update |
| 9 | History, cancellation, error/loading states | Passenger/driver can handle normal and failure flows |
| 10 | Full two-account test and Vercel deployment | A complete V1 ride works on deployed URLs |

### Definition of done for the frontend developer

The frontend is ready for the V1 demo when:

- Passenger and driver can authenticate.
- The correct role-specific screens are shown.
- Pickup/destination can be selected and the estimate is displayed.
- A passenger can create a ride without duplicate submissions.
- Driver can go online, send GPS, receive an offer and accept/reject it.
- Driver can move a ride through arrived, ongoing and completed.
- Passenger receives status and authorised driver-location updates.
- Cancellation, no-driver-found, expired-offer and API-error states are handled.
- Ride history works for both roles.
- The app is responsive, deployed over HTTPS, and tested against the deployed backend.

---

## 22. Coordination with the backend developer

Before integrating a feature, agree on the exact endpoint, request body, response fields, role and error cases. Use the separate **RideFlow V1 API Contract** as the shared source of truth.

Frontend developer should provide the backend developer with:

1. The local frontend origin and later the deployed Vercel origin (for CORS).
2. Required API request/response fields that are unclear in the contract.
3. Required Realtime events and the exact authenticated user/ride filters.
4. Expected behaviour for optional GPS fields (`heading_deg`, `speed_mps`) if the browser returns `null`.
5. Confirmation of the selected V1 address-selection approach (map click + manual label, unless a geocoding provider is separately agreed).

Backend developer should provide the frontend developer with:

1. Local and production API base URLs.
2. Working Supabase project URL and publishable key (safe to use in the frontend).
3. Test passenger and verified-driver accounts.
4. Confirmation that Realtime publication and RLS policies are configured for required subscriptions.
5. Confirmation that CORS allows localhost and the Vercel production origin.
6. The allowed GPS update frequency/rate limit and which GPS fields may be null.

Do not independently rename an endpoint or response field to make one side's code work. Update the shared contract first and tell the other developer.

---

## 23. Official references

Use these links when a library or hosted service changes. Follow the official docs over old tutorials.

- Vite Getting Started: https://vite.dev/guide/
- Supabase React quickstart: https://supabase.com/docs/guides/getting-started/quickstarts/reactjs
- Supabase Auth with React: https://supabase.com/docs/guides/auth/quickstarts/react
- Supabase Realtime database changes: https://supabase.com/docs/guides/realtime/subscribing-to-database-changes
- React-Leaflet installation: https://react-leaflet.js.org/docs/start-installation/
- React-Leaflet setup: https://react-leaflet.js.org/docs/start-setup/
- Browser Geolocation `watchPosition()`: https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/watchPosition
- Vercel Vite deployment: https://vercel.com/docs/frameworks/frontend/vite
- OpenStreetMap tile usage policy: https://operations.osmfoundation.org/policies/tiles/

---

**End of RideFlow V1 Frontend Developer Guide**
