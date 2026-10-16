"""Private, per-search provider records for explicit failed-request recovery."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json


SCHEMA_VERSION = 1


def _identity(provider: str, request: dict) -> str:
    return json.dumps([provider, request], sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderCache:
    def __init__(self, config: dict, saved: dict | None = None):
        self.config = config
        self.records = {}
        self.recovering = saved is not None
        self.retried = set()
        self.deferred = {}
        self._retry_started = False
        if saved is not None:
            self._load(saved)

    def _load(self, saved: dict) -> None:
        guidance = "; use --refresh to create a new search"
        if (not isinstance(saved, dict) or type(saved.get("schema_version")) is not int
                or saved["schema_version"] != SCHEMA_VERSION
                or not isinstance(saved.get("records"), list) or not saved["records"]):
            raise ValueError("cached search has no supported provider execution records" + guidance)
        # JSON comparison preserves distinctions such as false versus 0.
        if json.dumps(saved.get("config"), sort_keys=True, allow_nan=False) != json.dumps(
                self.config, sort_keys=True, allow_nan=False):
            raise ValueError("cached search provider or ranking configuration changed" + guidance)
        for row in saved["records"]:
            if (not isinstance(row, dict) or not isinstance(row.get("provider"), str)
                    or not isinstance(row.get("request"), dict)
                    or not isinstance(row.get("status"), str) or row["status"] not in {"ok", "empty", "error"}
                    or not isinstance(row.get("output"), list)
                    or any(not isinstance(item, dict) for item in row["output"])
                    or not isinstance(row.get("attempts"), list) or not row["attempts"]
                    or not isinstance(row.get("completed_at"), str)
                    or (row["status"] == "ok") != bool(row["output"])
                    or (row["status"] == "error" and not isinstance(row.get("error"), str))):
                raise ValueError("invalid cached provider execution record" + guidance)
            key = _identity(row["provider"], row["request"])
            if key in self.records:
                raise ValueError("duplicate cached provider execution record" + guidance)
            self.records[key] = deepcopy(row)

    @staticmethod
    def outcome(row: dict) -> dict:
        return deepcopy({key: row[key] for key in (
            "provider", "request", "status", "error", "completed_at"
        ) if key in row})

    def _execute(self, provider: str, request: dict, operation) -> dict:
        key = _identity(provider, request)
        started_at = _now()
        error = None
        try:
            output = list(operation())
            if any(not isinstance(item, dict) for item in output):
                raise ValueError("provider returned invalid records")
        except Exception as exc:
            output = []
            error = (str(exc) if provider == "anysearch" else f"{type(exc).__name__}: {exc}")[:240]
        completed_at = _now()
        status = "error" if error is not None else "ok" if output else "empty"
        attempt = {"started_at": started_at, "completed_at": completed_at, "status": status}
        if error is not None:
            attempt["error"] = error
        row = {
            "provider": provider, "request": deepcopy(request), "output": deepcopy(output),
            "status": status, "completed_at": completed_at,
            "attempts": [*self.records.get(key, {}).get("attempts", []), attempt],
        }
        if error is not None:
            row["error"] = error
        self.records[key] = row
        return row

    def call(self, provider: str, request: dict, operation) -> tuple[list[dict], dict]:
        key = _identity(provider, request)
        if key in self.records:
            row = self.records[key]
            return deepcopy(row["output"]), {**self.outcome(row), "reused": True}
        if self.recovering:
            outcome = {"provider": provider, "request": deepcopy(request), "status": "skipped",
                       "reason": "not_in_cached_execution; use --refresh for new requests"}
            self.deferred[key] = outcome
            return [], deepcopy(outcome)
        row = self._execute(provider, request, operation)
        return deepcopy(row["output"]), self.outcome(row)

    def has(self, provider: str, request: dict | None = None) -> bool:
        if request is not None:
            return _identity(provider, request) in self.records
        return any(row["provider"] == provider for row in self.records.values())

    def retry_failures(self, dispatch) -> None:
        if not self.recovering or self._retry_started:
            return
        self._retry_started = True
        # Freeze the old failed request set. New dependencies need --refresh.
        for key, row in list(self.records.items()):
            if row["status"] != "error":
                continue
            self._execute(row["provider"], row["request"], lambda row=row: dispatch(row))
            self.retried.add(key)

    def snapshot(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "config": deepcopy(self.config),
                "records": deepcopy(list(self.records.values()))}

    def outcomes(self) -> list[dict]:
        return [{**self.outcome(row), "retried": key in self.retried,
                 "cache_reused": key not in self.retried}
                for key, row in self.records.items()]
