"""Configuração lida do ambiente na inicialização do processo."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://fiapx:fiapx@localhost:5432/fiapx")
AUTH_INTROSPECTION_URL = os.getenv("AUTH_INTROSPECTION_URL", "http://localhost:8081/api/v1/auth/introspect")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://fiapx:fiapx@localhost:5672/%2F")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://localhost:9000")
S3_PUBLIC_ENDPOINT_URL = os.getenv("S3_PUBLIC_ENDPOINT_URL", S3_ENDPOINT_URL)
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "fiapx")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "fiapx-minio-password")
S3_BUCKET = os.getenv("S3_BUCKET", "videos")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Janela em que um token revogado ainda passa. Curta o bastante para não virar
# problema, longa o bastante para tirar a introspecção do caminho crítico.
TOKEN_CACHE_TTL_SECONDS = int(os.getenv("TOKEN_CACHE_TTL_SECONDS", "60"))
UPDATES_CHANNEL = "video.updates"

EXCHANGE = "video.events"
STATUS_QUEUE = "video-status-queue"
STATUS_ROUTING_KEY = "video.status.changed"
RECEIVED_ROUTING_KEY = "video.received"

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm"}
PRESIGNED_URL_TTL_SECONDS = int(os.getenv("PRESIGNED_URL_TTL_SECONDS", "3600"))
