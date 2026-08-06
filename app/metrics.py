"""Métricas expostas ao Prometheus em /metrics."""
import time

from fastapi import Request
from prometheus_client import Counter, Histogram, make_asgi_app

uploads = Counter("fiapx_video_uploads_total", "Vídeos aceitos para processamento")
upload_bytes = Histogram(
    "fiapx_video_upload_bytes",
    "Tamanho dos vídeos recebidos",
    buckets=(1e6, 1e7, 5e7, 1e8, 2.5e8, 5e8),
)
status_transitions = Counter(
    "fiapx_video_status_transitions_total",
    "Transições de estado consumidas do barramento",
    ["status"],
)
requests = Histogram(
    "fiapx_http_request_duration_seconds",
    "Duração das requisições HTTP",
    ["method", "endpoint", "status"],
)

app = make_asgi_app()


async def measure_requests(request: Request, call_next):
    """Mede por rota declarada, e não pelo caminho pedido, para não explodir a cardinalidade."""
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    endpoint = getattr(route, "path", "desconhecido")
    requests.labels(request.method, endpoint, str(response.status_code)).observe(time.perf_counter() - started)
    return response
