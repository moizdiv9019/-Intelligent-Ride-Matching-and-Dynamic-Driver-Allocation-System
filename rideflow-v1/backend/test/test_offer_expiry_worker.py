import asyncio
from uuid import UUID

from app.services import dispatch_service
from app.workers import offer_expiry_worker


OFFER_ID = UUID("8f8e9d26-eef8-4ef9-b567-cbc5ab05d9e5")
RIDE_ID = UUID("a8493516-3fd4-4a14-9b0a-a7e5dfac01ab")


class MockResult:
    def __init__(self, *, row=None, rows=(), values=()):
        self.row = row
        self.rows = list(rows)
        self.values = list(values)

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.row

    def scalars(self):
        return self


class MockSession:
    def __init__(self):
        self.execute_calls = []
        self.events = []
        self.commit_count = 0
        self.rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        normalized_sql = sql.lower()
        params = params or {}
        self.execute_calls.append((sql, params))

        if normalized_sql.startswith("select id, ride_id"):
            return MockResult(rows=[{"id": OFFER_ID, "ride_id": RIDE_ID}])
        if normalized_sql.startswith("update public.ride_offers"):
            return MockResult(row={"id": OFFER_ID})
        if "insert into public.ride_events" in normalized_sql:
            self.events.append(params)
            return MockResult()
        if normalized_sql.startswith("select ride.id"):
            return MockResult(values=[RIDE_ID])
        raise AssertionError(f"Unexpected SQL statement: {sql}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1


def test_expiry_worker_marks_expired_records_event_and_dispatches(monkeypatch):
    db = MockSession()
    dispatch_calls = []

    async def dispatch_next(session, ride_id):
        dispatch_calls.append((session, ride_id))
        return dispatch_service.DispatchResult(status="no_driver_found")

    monkeypatch.setattr(offer_expiry_worker, "dispatch_next_driver", dispatch_next)

    processed = asyncio.run(
        offer_expiry_worker.process_offer_expiry_once(lambda: db)
    )

    expiry_query = next(
        sql for sql, _ in db.execute_calls if sql.startswith("SELECT id, ride_id")
    )
    assert "expires_at <= now()" in expiry_query
    assert "FOR UPDATE SKIP LOCKED" in expiry_query
    assert any(
        "SET status = 'expired', responded_at = now()" in sql
        for sql, _ in db.execute_calls
    )
    assert db.events[0]["event_type"] == "driver_offer_expired"
    assert dispatch_calls == [(db, RIDE_ID)]
    assert processed == 1
    assert db.commit_count == 2


def test_worker_retries_after_cycle_exception(monkeypatch):
    stop_event = asyncio.Event()
    calls = []

    async def fail_once(session_factory):
        calls.append(True)
        stop_event.set()
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(offer_expiry_worker, "process_offer_expiry_once", fail_once)
    asyncio.run(
        offer_expiry_worker.run_offer_expiry_worker(
            stop_event,
            session_factory=lambda: None,
            poll_interval_seconds=0.01,
        )
    )

    assert calls == [True]
