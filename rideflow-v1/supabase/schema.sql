-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.profiles (
  id uuid NOT NULL,
  full_name text NOT NULL,
  phone_number character varying,
  avatar_url text,
  account_status text NOT NULL DEFAULT 'active'::text CHECK (account_status = ANY (ARRAY['active'::text, 'restricted'::text, 'suspended'::text])),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT profiles_pkey PRIMARY KEY (id),
  CONSTRAINT profiles_id_fkey FOREIGN KEY (id) REFERENCES auth.users(id)
);
CREATE TABLE public.user_roles (
  user_id uuid NOT NULL,
  role text NOT NULL CHECK (role = ANY (ARRAY['passenger'::text, 'driver'::text, 'admin'::text])),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role),
  CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.driver_profiles (
  user_id uuid NOT NULL,
  verification_status text NOT NULL DEFAULT 'pending'::text CHECK (verification_status = ANY (ARRAY['pending'::text, 'verified'::text, 'rejected'::text])),
  availability_status text NOT NULL DEFAULT 'offline'::text CHECK (availability_status = ANY (ARRAY['offline'::text, 'available'::text, 'busy'::text])),
  last_became_available_at timestamp with time zone,
  last_seen_at timestamp with time zone,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT driver_profiles_pkey PRIMARY KEY (user_id),
  CONSTRAINT driver_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.profiles(id)
);
CREATE TABLE public.vehicles (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  driver_user_id uuid NOT NULL,
  vehicle_type text NOT NULL CHECK (vehicle_type = ANY (ARRAY['bike'::text, 'auto'::text, 'car'::text, 'accessible_van'::text, 'other'::text])),
  make_model text,
  registration_number text UNIQUE,
  seat_capacity smallint NOT NULL DEFAULT 4 CHECK (seat_capacity >= 1 AND seat_capacity <= 20),
  wheelchair_accessible boolean NOT NULL DEFAULT false,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT vehicles_pkey PRIMARY KEY (id),
  CONSTRAINT vehicles_driver_user_id_fkey FOREIGN KEY (driver_user_id) REFERENCES public.driver_profiles(user_id)
);
CREATE TABLE public.driver_locations (
  driver_user_id uuid NOT NULL,
  location USER-DEFINED NOT NULL,
  accuracy_m numeric,
  heading_deg numeric,
  speed_mps numeric,
  device_recorded_at timestamp with time zone NOT NULL,
  received_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT driver_locations_pkey PRIMARY KEY (driver_user_id),
  CONSTRAINT driver_locations_driver_user_id_fkey FOREIGN KEY (driver_user_id) REFERENCES public.driver_profiles(user_id)
);
CREATE TABLE public.rides (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  passenger_id uuid NOT NULL,
  assigned_driver_id uuid,
  assigned_vehicle_id uuid,
  status text NOT NULL DEFAULT 'requested'::text CHECK (status = ANY (ARRAY['requested'::text, 'accepted'::text, 'arriving'::text, 'ongoing'::text, 'completed'::text, 'cancelled'::text, 'no_driver_found'::text])),
  pickup_location USER-DEFINED NOT NULL,
  pickup_address text NOT NULL,
  destination_location USER-DEFINED NOT NULL,
  destination_address text NOT NULL,
  estimated_distance_m integer CHECK (estimated_distance_m >= 0),
  estimated_duration_s integer CHECK (estimated_duration_s >= 0),
  estimated_fare numeric CHECK (estimated_fare >= 0::numeric),
  final_distance_m integer CHECK (final_distance_m >= 0),
  final_fare numeric CHECK (final_fare >= 0::numeric),
  currency character NOT NULL DEFAULT 'INR'::bpchar,
  fare_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
  route_geom USER-DEFINED,
  requested_at timestamp with time zone NOT NULL DEFAULT now(),
  accepted_at timestamp with time zone,
  arrived_at timestamp with time zone,
  started_at timestamp with time zone,
  completed_at timestamp with time zone,
  cancelled_at timestamp with time zone,
  cancelled_by uuid,
  cancellation_reason text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  client_request_id uuid NOT NULL,
  CONSTRAINT rides_pkey PRIMARY KEY (id),
  CONSTRAINT rides_passenger_id_fkey FOREIGN KEY (passenger_id) REFERENCES public.profiles(id),
  CONSTRAINT rides_assigned_driver_id_fkey FOREIGN KEY (assigned_driver_id) REFERENCES public.driver_profiles(user_id),
  CONSTRAINT rides_assigned_vehicle_id_fkey FOREIGN KEY (assigned_vehicle_id) REFERENCES public.vehicles(id),
  CONSTRAINT rides_cancelled_by_fkey FOREIGN KEY (cancelled_by) REFERENCES public.profiles(id)
);
CREATE TABLE public.ride_offers (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  ride_id uuid NOT NULL,
  driver_user_id uuid NOT NULL,
  offer_number smallint NOT NULL CHECK (offer_number > 0),
  status text NOT NULL DEFAULT 'offered'::text CHECK (status = ANY (ARRAY['offered'::text, 'accepted'::text, 'rejected'::text, 'expired'::text, 'withdrawn'::text])),
  pickup_distance_m integer CHECK (pickup_distance_m >= 0),
  pickup_eta_s integer CHECK (pickup_eta_s >= 0),
  match_score numeric CHECK (match_score >= 0::numeric AND match_score <= 100::numeric),
  match_factors jsonb NOT NULL DEFAULT '{}'::jsonb,
  offered_at timestamp with time zone NOT NULL DEFAULT now(),
  expires_at timestamp with time zone NOT NULL,
  responded_at timestamp with time zone,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT ride_offers_pkey PRIMARY KEY (id),
  CONSTRAINT ride_offers_ride_id_fkey FOREIGN KEY (ride_id) REFERENCES public.rides(id),
  CONSTRAINT ride_offers_driver_user_id_fkey FOREIGN KEY (driver_user_id) REFERENCES public.driver_profiles(user_id)
);
CREATE TABLE public.ride_events (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  ride_id uuid NOT NULL,
  actor_user_id uuid,
  actor_type text NOT NULL CHECK (actor_type = ANY (ARRAY['passenger'::text, 'driver'::text, 'admin'::text, 'system'::text])),
  event_type text NOT NULL,
  from_status text,
  to_status text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT ride_events_pkey PRIMARY KEY (id),
  CONSTRAINT ride_events_ride_id_fkey FOREIGN KEY (ride_id) REFERENCES public.rides(id),
  CONSTRAINT ride_events_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.profiles(id)
);