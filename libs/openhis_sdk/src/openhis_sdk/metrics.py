"""
Prometheus metrics for OpenHIS services — canonical source.

Two-line wiring (every native service main.py)::

    from openhis_sdk.metrics import MetricsMiddleware, metrics_router
    app.add_middleware(MetricsMiddleware, service="my-service")
    app.include_router(metrics_router)

What you get:

* ``openhis_http_requests_total{service,method,path,status}`` — request
  counter labeled by the *matched route template* (e.g. ``/api/patients/{mrn}``,
  never the raw URL) to bound label cardinality. Unrouted requests are
  labeled ``path="unmatched"``.
* ``openhis_http_request_duration_seconds{service,method,path}`` — latency
  histogram (fixed buckets 5 ms … 10 s).
* ``GET /metrics`` — Prometheus text exposition. The path is exempt from
  ``openhis_sdk.auth.JWTMiddleware`` (see ``_SKIP_PREFIXES``) so an
  in-network scraper needs no token. It is **not** proxied by nginx —
  ``nginx.conf.j2`` returns 404 for ``/<svc>/metrics`` — so the endpoint is
  reachable only on the compose network (e.g. ``http://mpi:8007/metrics``).
* ``openhis_dlq_depth{stream}`` — pull-based gauge: at scrape time the
  module XLENs the dead-letter stream ``openhis:events:dlq`` (SDK
  BusConsumer) over a short-lived sync Redis client (1 s timeout) when
  ``REDIS_URL`` is set. On any failure
  (or when ``REDIS_URL`` is unset) the samples are simply absent — a scrape
  must never break because Redis is down.

Alerting
--------
A non-empty DLQ means at least one bus event permanently failed processing
and is waiting for an operator. Recommended Prometheus alert rule (shipped
as ``infra/prometheus/alerts-example.yml``)::

    - alert: OpenHISDeadLetterQueueNotEmpty
      expr: openhis_dlq_depth > 0
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "OpenHIS DLQ {{ $labels.stream }} has parked events"

Implementation note: ``prometheus_client`` is the primary backend (declared
in the SDK's pyproject dependencies). If it is absent the module falls back
to a zero-dependency, thread-safe implementation that renders Prometheus
text exposition format 0.0.4 — the SDK stays importable either way.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from fastapi import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger("openhis_sdk.metrics")

# Dead-letter streams sampled by the openhis_dlq_depth gauge at scrape time.
DLQ_STREAMS: tuple[str, ...] = ("openhis:events:dlq",)
_DLQ_SCRAPE_TIMEOUT_S = 1.0

# Latency histogram buckets (seconds) — shared by both backends.
LATENCY_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

try:  # pragma: no cover - exercised implicitly by the import
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from prometheus_client.core import GaugeMetricFamily

    HAVE_PROMETHEUS_CLIENT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_PROMETHEUS_CLIENT = False

FALLBACK_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ── zero-dependency fallback backend ──────────────────────────────────────────
# Always defined (cheap) so the text-exposition renderer is unit-testable
# even when prometheus_client is installed.


def _fmt_value(value: float) -> str:
    return str(float(value))


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labelnames: Sequence[str], labelvalues: Sequence[str]) -> str:
    if not labelnames:
        return ""
    pairs = ",".join(
        f'{n}="{_escape_label_value(str(v))}"'
        for n, v in zip(labelnames, labelvalues)
    )
    return "{" + pairs + "}"


class _FallbackMetric:
    """Base for the zero-dep Counter/Gauge/Histogram (dict + Lock)."""

    typ = "untyped"

    def __init__(self, name: str, documentation: str,
                 labelnames: Sequence[str] = ()) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._lock = threading.Lock()
        self._values: dict[tuple[str, ...], Any] = {}

    def labels(self, *labelvalues: str, **labelkwargs: str) -> "_FallbackChild":
        if labelkwargs:
            if labelvalues:
                raise ValueError("pass label values either positionally or by name, not both")
            try:
                labelvalues = tuple(str(labelkwargs[n]) for n in self.labelnames)
            except KeyError as exc:
                raise ValueError(f"missing label {exc} for metric {self.name}") from exc
        key = tuple(str(v) for v in labelvalues)
        if len(key) != len(self.labelnames):
            raise ValueError(
                f"metric {self.name} expects labels {self.labelnames}, got {key}"
            )
        return _FallbackChild(self, key)

    # unlabeled convenience (mirrors prometheus_client behaviour)
    def inc(self, amount: float = 1.0) -> None:
        self.labels().inc(amount)

    def set(self, value: float) -> None:
        self.labels().set(value)

    def observe(self, value: float) -> None:
        self.labels().observe(value)

    # hooks implemented by subclasses
    def _inc(self, key: tuple[str, ...], amount: float) -> None:
        raise NotImplementedError

    def _set(self, key: tuple[str, ...], value: float) -> None:
        raise NotImplementedError

    def _observe(self, key: tuple[str, ...], value: float) -> None:
        raise NotImplementedError

    def render(self) -> str:
        raise NotImplementedError

    def _header(self) -> str:
        return (
            f"# HELP {self.name} {self.documentation}\n"
            f"# TYPE {self.name} {self.typ}\n"
        )


class _FallbackChild:
    __slots__ = ("_metric", "_key")

    def __init__(self, metric: _FallbackMetric, key: tuple[str, ...]) -> None:
        self._metric = metric
        self._key = key

    def inc(self, amount: float = 1.0) -> None:
        self._metric._inc(self._key, amount)

    def set(self, value: float) -> None:
        self._metric._set(self._key, value)

    def observe(self, value: float) -> None:
        self._metric._observe(self._key, value)


class _FallbackCounter(_FallbackMetric):
    typ = "counter"

    def _inc(self, key: tuple[str, ...], amount: float) -> None:
        if amount < 0:
            raise ValueError("counters can only increase")
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> str:
        with self._lock:
            items = sorted(self._values.items())
        lines = [self._header()]
        for key, value in items:
            lines.append(
                f"{self.name}{_render_labels(self.labelnames, key)} {_fmt_value(value)}\n"
            )
        return "".join(lines)


class _FallbackGauge(_FallbackMetric):
    typ = "gauge"

    def _set(self, key: tuple[str, ...], value: float) -> None:
        with self._lock:
            self._values[key] = float(value)

    def _inc(self, key: tuple[str, ...], amount: float) -> None:
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> str:
        with self._lock:
            items = sorted(self._values.items())
        lines = [self._header()]
        for key, value in items:
            lines.append(
                f"{self.name}{_render_labels(self.labelnames, key)} {_fmt_value(value)}\n"
            )
        return "".join(lines)


class _FallbackHistogram(_FallbackMetric):
    typ = "histogram"

    def __init__(self, name: str, documentation: str,
                 labelnames: Sequence[str] = (),
                 buckets: Sequence[float] = LATENCY_BUCKETS) -> None:
        super().__init__(name, documentation, labelnames)
        self.buckets = tuple(sorted(buckets))

    def _observe(self, key: tuple[str, ...], value: float) -> None:
        with self._lock:
            state = self._values.get(key)
            if state is None:
                state = {"counts": [0] * len(self.buckets), "sum": 0.0, "count": 0}
                self._values[key] = state
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    state["counts"][i] += 1
            state["sum"] += value
            state["count"] += 1

    def render(self) -> str:
        with self._lock:
            items = sorted(
                (k, list(v["counts"]), v["sum"], v["count"])
                for k, v in self._values.items()
            )
        lines = [self._header()]
        for key, counts, total, count in items:
            # counts[i] is already cumulative: every bound >= value is
            # incremented in _observe().
            for bound, bucket_count in zip(self.buckets, counts):
                le = _render_labels(
                    self.labelnames + ("le",), key + (str(bound),)
                )
                lines.append(f"{self.name}_bucket{le} {_fmt_value(bucket_count)}\n")
            inf = _render_labels(self.labelnames + ("le",), key + ("+Inf",))
            lines.append(f"{self.name}_bucket{inf} {_fmt_value(count)}\n")
            base = _render_labels(self.labelnames, key)
            lines.append(f"{self.name}_sum{base} {_fmt_value(total)}\n")
            lines.append(f"{self.name}_count{base} {_fmt_value(count)}\n")
        return "".join(lines)


@dataclass
class _CallbackGauge:
    """A gauge whose samples are produced by a callback at scrape time."""

    name: str
    documentation: str
    labelnames: tuple[str, ...]
    fn: Callable[[], Any]

    def samples(self) -> list[tuple[tuple[str, ...], float]]:
        try:
            result = self.fn()
        except Exception as exc:  # a scrape must never break
            log.warning("callback gauge %s failed: %s", self.name, exc)
            return []
        if result is None:
            return []
        if isinstance(result, (int, float)):
            return [((), float(result))]
        out: list[tuple[tuple[str, ...], float]] = []
        for labelvalues, value in result:
            out.append((tuple(str(v) for v in labelvalues), float(value)))
        return out

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.documentation}\n"
            f"# TYPE {self.name} gauge\n"
        ]
        for key, value in self.samples():
            lines.append(
                f"{self.name}{_render_labels(self.labelnames, key)} {_fmt_value(value)}\n"
            )
        return "".join(lines)


class _FallbackRegistry:
    """Minimal stand-in for prometheus_client.CollectorRegistry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: list[_FallbackMetric] = []
        self.callbacks: list[_CallbackGauge] = []

    def counter(self, name: str, documentation: str,
                labelnames: Sequence[str] = ()) -> _FallbackCounter:
        m = _FallbackCounter(name, documentation, labelnames)
        with self._lock:
            self._metrics.append(m)
        return m

    def gauge(self, name: str, documentation: str,
              labelnames: Sequence[str] = ()) -> _FallbackGauge:
        m = _FallbackGauge(name, documentation, labelnames)
        with self._lock:
            self._metrics.append(m)
        return m

    def histogram(self, name: str, documentation: str,
                  labelnames: Sequence[str] = (),
                  buckets: Sequence[float] = LATENCY_BUCKETS) -> _FallbackHistogram:
        m = _FallbackHistogram(name, documentation, labelnames, buckets)
        with self._lock:
            self._metrics.append(m)
        return m

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics)
            callbacks = list(self.callbacks)
        parts = [m.render() for m in metrics]
        parts += [cb.render() for cb in callbacks]
        return "".join(parts)


# ── callback gauges (shared by both backends) ─────────────────────────────────

_CALLBACK_GAUGES: list[_CallbackGauge] = []
_CALLBACK_LOCK = threading.Lock()


def register_callback_gauge(
    name: str,
    documentation: str,
    fn: Callable[[], Any],
    labelnames: Sequence[str] = (),
) -> None:
    """Register a gauge evaluated at scrape time.

    ``fn`` is called on every ``GET /metrics``. It must return either a
    single number (when ``labelnames`` is empty) or an iterable of
    ``(labelvalues_tuple, value)`` pairs. Exceptions are swallowed (logged
    at WARNING) — a failing callback yields no samples, never a 500.
    Re-registering the same ``name`` replaces the previous callback.
    """
    entry = _CallbackGauge(name, documentation, tuple(labelnames), fn)
    with _CALLBACK_LOCK:
        _CALLBACK_GAUGES[:] = [g for g in _CALLBACK_GAUGES if g.name != name]
        _CALLBACK_GAUGES.append(entry)


# ── DLQ depth gauge (pull-based, scrape-time XLEN) ────────────────────────────


def _open_redis(url: str) -> Any:
    """Short-lived sync Redis client for scrape-time sampling (1 s timeout)."""
    import redis as redis_sync

    return redis_sync.Redis.from_url(
        url,
        socket_timeout=_DLQ_SCRAPE_TIMEOUT_S,
        socket_connect_timeout=_DLQ_SCRAPE_TIMEOUT_S,
    )


def dlq_depth_samples() -> list[tuple[tuple[str, ...], float]]:
    """XLEN each known DLQ stream. Empty list when REDIS_URL is unset
    or Redis is unreachable — the scrape itself must never fail."""
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return []
    samples: list[tuple[tuple[str, ...], float]] = []
    client = None
    try:
        client = _open_redis(url)
        for stream in DLQ_STREAMS:
            samples.append(((stream,), float(client.xlen(stream))))
    except Exception as exc:  # connection refused, timeout, bad URL, …
        log.warning("DLQ depth scrape failed: %s", exc)
        return []
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: S110 - best-effort cleanup
                pass
    return samples


register_callback_gauge(
    "openhis_dlq_depth",
    "Entries parked on each OpenHIS dead-letter stream (XLEN at scrape time)",
    dlq_depth_samples,
    labelnames=("stream",),
)


# ── backend selection ─────────────────────────────────────────────────────────

_GAUGES: dict[str, Any] = {}
_GAUGES_LOCK = threading.Lock()

if HAVE_PROMETHEUS_CLIENT:
    # Dedicated registry: keeps OpenHIS metrics isolated from the global
    # default REGISTRY (and avoids duplicate-timeseries errors when test
    # suites import service apps repeatedly).
    REGISTRY: Any = CollectorRegistry()

    _http_requests: Any = Counter(
        "openhis_http_requests_total",
        "Total HTTP requests handled, by service/method/route-template/status",
        ("service", "method", "path", "status"),
        registry=REGISTRY,
    )
    _http_latency: Any = Histogram(
        "openhis_http_request_duration_seconds",
        "HTTP request latency in seconds, by service/method/route-template",
        ("service", "method", "path"),
        buckets=LATENCY_BUCKETS,
        registry=REGISTRY,
    )

    class _CallbackCollector:
        """Bridges register_callback_gauge() into the prometheus registry."""

        def collect(self) -> Iterable[Any]:
            with _CALLBACK_LOCK:
                gauges = list(_CALLBACK_GAUGES)
            for cb in gauges:
                family = GaugeMetricFamily(
                    cb.name, cb.documentation, labels=list(cb.labelnames)
                )
                for labelvalues, value in cb.samples():
                    family.add_metric(list(labelvalues), value)
                yield family

        def describe(self) -> Iterable[Any]:
            # Nothing static to describe; samples are scrape-time only.
            return []

    REGISTRY.register(_CallbackCollector())

    def gauge(name: str, documentation: str,
              labelnames: Sequence[str] = ()) -> Any:
        """Idempotent settable-gauge factory bound to the OpenHIS registry."""
        with _GAUGES_LOCK:
            if name not in _GAUGES:
                _GAUGES[name] = Gauge(
                    name, documentation, tuple(labelnames), registry=REGISTRY
                )
            return _GAUGES[name]

    def render_metrics() -> tuple[bytes, str]:
        """Return (payload, content_type) for the /metrics response."""
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST

else:
    REGISTRY = _FallbackRegistry()
    REGISTRY.callbacks = _CALLBACK_GAUGES  # rendered at scrape time

    _http_requests = REGISTRY.counter(
        "openhis_http_requests_total",
        "Total HTTP requests handled, by service/method/route-template/status",
        ("service", "method", "path", "status"),
    )
    _http_latency = REGISTRY.histogram(
        "openhis_http_request_duration_seconds",
        "HTTP request latency in seconds, by service/method/route-template",
        ("service", "method", "path"),
        buckets=LATENCY_BUCKETS,
    )

    def gauge(name: str, documentation: str,
              labelnames: Sequence[str] = ()) -> Any:
        """Idempotent settable-gauge factory bound to the OpenHIS registry."""
        with _GAUGES_LOCK:
            if name not in _GAUGES:
                _GAUGES[name] = REGISTRY.gauge(name, documentation, tuple(labelnames))
            return _GAUGES[name]

    def render_metrics() -> tuple[bytes, str]:
        """Return (payload, content_type) for the /metrics response."""
        return REGISTRY.render().encode("utf-8"), FALLBACK_CONTENT_TYPE


# ── ASGI middleware ────────────────────────────────────────────────────────────


def _route_template(request: Request) -> str:
    """Matched route template ('/api/patients/{mrn}'), else 'unmatched'.

    Starlette sets scope['route'] during routing; BaseHTTPMiddleware shares
    the scope dict, so the key is visible after call_next returns. Using
    the template (not the raw path) bounds label cardinality.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request count + latency histogram for every request.

    Usage::

        app.add_middleware(MetricsMiddleware, service="mpi")

    Skips ``/metrics`` itself so the scraper does not inflate the numbers.
    Requests that raise are recorded with status="500" before re-raising.
    """

    def __init__(self, app: Any, service: str) -> None:
        super().__init__(app)
        self._service = service

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            elapsed = time.perf_counter() - start
            path = _route_template(request)
            _http_requests.labels(
                service=self._service,
                method=request.method,
                path=path,
                status=status,
            ).inc()
            _http_latency.labels(
                service=self._service,
                method=request.method,
                path=path,
            ).observe(elapsed)


# ── /metrics endpoint ──────────────────────────────────────────────────────────

metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    """Prometheus text exposition. Internal scrape only — JWT-exempt via
    openhis_sdk.auth._SKIP_PREFIXES and blocked at nginx (returns 404)."""
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
