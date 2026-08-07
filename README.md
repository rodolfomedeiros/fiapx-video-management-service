# fiapx-video-management-service

API do **FIAP X**: recebe uploads de vídeo, publica o trabalho no barramento, acompanha o
estado de cada processamento e entrega o `.zip` final. É também o serviço que empurra as
atualizações em tempo real para o navegador.

**Python 3.13** · FastAPI · SQLAlchemy 2.0 (asyncpg) · aio-pika · boto3 · redis

## Repositórios do projeto

| Repositório | Linguagem | Papel |
| :--- | :--- | :--- |
| [fiapx-platform](https://github.com/rodolfomedeiros/fiapx-platform) | — | Compose, Kubernetes, contratos, topologia do broker |
| [fiapx-auth-service](https://github.com/rodolfomedeiros/fiapx-auth-service) | Java 25 · Spring Boot 4 | Cadastro, login, emissão e introspecção de JWT |
| **fiapx-video-management-service** *(você está aqui)* | Python 3.13 · FastAPI | Upload, listagem, download e WebSocket de tempo real |
| [fiapx-video-processor-worker](https://github.com/rodolfomedeiros/fiapx-video-processor-worker) | Rust 1.94 · Tokio | Extração de quadros com FFmpeg e compactação em `.zip` |
| [fiapx-notification-service](https://github.com/rodolfomedeiros/fiapx-notification-service) | Go 1.25 | Consumo da DLQ e envio de e-mail de falha |
| [fiapx-web](https://github.com/rodolfomedeiros/fiapx-web) | React 19 · TypeScript 6 | Interface de upload, acompanhamento e download |

> Para subir o sistema inteiro, use o **fiapx-platform**. Este repositório sozinho precisa
> de PostgreSQL, RabbitMQ, Redis e MinIO acessíveis.

## Onde este serviço entra

```mermaid
flowchart TB
    Client(["Cliente"])
    GW["API Gateway<br/>Nginx · Ingress"]
    Auth["auth-service<br/>Java 25"]
    VMS["video-management-service<br/>Python 3.13 · FastAPI"]
    Worker["video-processor-worker<br/>Rust 1.94"]
    Notif["notification-service<br/>Go 1.25"]
    MQ{{"RabbitMQ<br/>video.events"}}
    Redis[("Redis 7.4<br/>cache · pub/sub")]
    PG[("PostgreSQL 17")]
    S3[("MinIO · S3")]

    Client -->|HTTP + WebSocket| GW
    GW --> Auth
    GW --> VMS
    VMS -.->|introspecção| Auth
    VMS ---|"tabela videos"| PG
    VMS <-->|"claims + video.updates"| Redis
    VMS -->|grava o original| S3
    VMS -->|"publica<br/>video.received"| MQ
    MQ -->|"entrega<br/>video.status.changed"| VMS
    MQ --> Worker
    MQ --> Notif
    Worker --- S3

    classDef atual stroke:#e8590c,stroke-width:4px
    class VMS atual
```

## API

Caminhos internos são `/api/v1/videos/*`; pelo gateway do Compose, `/videos/*`.
A documentação interativa fica em **`/docs`** (Swagger, gerado automaticamente).

Todas as rotas exigem `Authorization: Bearer <token>` obtido no auth-service.

### `POST /api/v1/videos/upload`

`multipart/form-data` com o campo `file`. Responde `202` com `{ "id", "status": "RECEIVED" }`.

| Regra | Resposta |
| :--- | :--- |
| Extensão em `.mp4 .avi .mov .mkv .wmv .flv .webm` | `400` se outra |
| Tamanho até `MAX_UPLOAD_BYTES` (500 MB) | `413` se maior |

O arquivo vai em **streaming** para o object storage: o handle já spoolado em disco pelo
Starlette é repassado direto ao `upload_fileobj`, então um vídeo de 500 MB não vira 500 MB
de RAM.

### `GET /api/v1/videos`

Lista apenas os vídeos do usuário autenticado, do mais recente para o mais antigo.

| Parâmetro | Padrão | Descrição |
| :--- | :--- | :--- |
| `page` | `1` | Página, começando em 1 |
| `size` | `20` | Itens por página, máximo 100 |
| `status` | — | Filtra por `RECEIVED`, `PROCESSING`, `COMPLETED` ou `ERROR` |

Resposta: `{ "items": [...], "page", "size", "total" }`.

### `GET /api/v1/videos/{id}/download`

Devolve `{ "url", "expires_in" }` com uma URL assinada do MinIO/S3, válida por
`PRESIGNED_URL_TTL_SECONDS`.

| Situação | Resposta |
| :--- | :--- |
| Vídeo inexistente ou de outro usuário | `404` |
| Ainda não concluído | `409` |

### `GET /api/v1/videos/ws` — WebSocket

Autenticação por header `Authorization` **ou** por query string `?token=<jwt>`, porque a
API de WebSocket do navegador não permite enviar headers.

A cada transição de estado, o socket recebe o vídeo serializado:

```json
{ "id": "...", "original_name": "ferias.mp4", "status": "COMPLETED",
  "zip_file_path": "processed/<user>/<video>.zip", "error_message": null,
  "created_at": "2026-08-06T14:46:08+00:00" }
```

### Operacionais

| Endpoint | Uso |
| :--- | :--- |
| `GET /health` | Probe de readiness e liveness |
| `GET /metrics` | Métricas do Prometheus |
| `GET /docs` | Swagger |

## Papel do Redis

O Redis cumpre **duas funções distintas**, ambas opcionais:

```mermaid
sequenceDiagram
    participant Q as RabbitMQ
    participant A as réplica A
    participant R as Redis
    participant B as réplica B
    participant C as Cliente conectado em B

    Q->>A: video.status.changed
    Note over A: só uma réplica<br/>consome a mensagem
    A->>A: persiste a transição
    A->>R: publica em video.updates
    R->>B: entrega a todas as réplicas
    B-->>C: WebSocket · COMPLETED
```

1. **Fanout do WebSocket.** Sem ele, apenas a réplica que consumiu o evento conseguiria
   avisar seus próprios sockets, e um usuário conectado a qualquer outra nunca veria o
   vídeo sair de `PROCESSING`.
2. **Cache da introspecção.** Guarda as claims já validadas por `TOKEN_CACHE_TTL_SECONDS`,
   tirando uma chamada HTTP ao auth-service do caminho de cada requisição. A chave é o
   **digest SHA-256** do token, não o token, para que ler o cache não entregue credenciais
   utilizáveis. Respostas negativas não são cacheadas.

Se o Redis estiver fora, a API continua respondendo: a introspecção volta a bater no
auth-service a cada requisição e o alcance do WebSocket fica restrito à réplica.

## Configuração

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://fiapx:fiapx@localhost:5432/fiapx` | PostgreSQL |
| `AUTH_INTROSPECTION_URL` | `http://localhost:8081/api/v1/auth/introspect` | Endpoint de introspecção |
| `RABBITMQ_URL` | `amqp://fiapx:fiapx@localhost:5672/%2F` | Barramento |
| `REDIS_URL` | `redis://localhost:6379/0` | Cache e pub/sub |
| `S3_ENDPOINT_URL` | `http://localhost:9000` | Endpoint interno do object storage |
| `S3_PUBLIC_ENDPOINT_URL` | igual ao anterior | Host usado na URL assinada entregue ao cliente |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `fiapx` / `fiapx-minio-password` | Credenciais |
| `S3_BUCKET` | `videos` | Bucket |
| `MAX_UPLOAD_BYTES` | `524288000` (500 MB) | Limite por upload |
| `PRESIGNED_URL_TTL_SECONDS` | `3600` | Validade da URL de download |
| `TOKEN_CACHE_TTL_SECONDS` | `60` | Janela em que um token revogado ainda passa |

## Organização

| Módulo | Responsabilidade |
| :--- | :--- |
| `app/config.py` | Configuração lida do ambiente |
| `app/models.py` | Mapeamento da tabela `videos` |
| `app/db.py` | Engine e sessão assíncrona |
| `app/security.py` | Introspecção do Bearer token, com cache |
| `app/storage.py` | Envio em streaming e URL assinada no MinIO/S3 |
| `app/messaging.py` | Conexão única com o RabbitMQ: publicação e consumo |
| `app/events.py` | Envelope dos eventos, conforme o contrato compartilhado |
| `app/cache.py` | Cache de introspecção e distribuição entre réplicas |
| `app/hub.py` | Conexões WebSocket abertas por usuário |
| `app/metrics.py` | Métricas expostas em `/metrics` |
| `app/main.py` | Rotas HTTP e ciclo de vida da aplicação |

A conexão com o RabbitMQ é **única e robusta**, aberta no `lifespan` — não uma por mensagem
publicada, que sob rajadas de upload esgotaria os file descriptors do broker.

## Executar

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Com a infraestrutura do Compose de pé, os padrões das variáveis funcionam sem configuração
adicional.

## Testes

```sh
pip install -r requirements-dev.txt
pytest
```

54 testes cobrindo rotas, introspecção e seu cache, hub de WebSocket, consumo de transições
de estado, métricas e conformidade dos eventos com o contrato. A suíte roda sobre SQLite
temporário, sem depender de PostgreSQL, RabbitMQ, Redis ou MinIO.

## Contrato de eventos

`contracts/video-event.schema.json` é uma cópia do contrato canônico mantido em
[fiapx-platform](https://github.com/rodolfomedeiros/fiapx-platform).
`tests/test_events.py` valida contra ele os eventos publicados e, quando o repositório da
plataforma está presente no checkout, confere se as duas cópias continuam idênticas.

Este serviço **publica** `video.received` e **consome** `video.status.changed`.

Uma mensagem de status ilegível vai direto para a DLQ — retentar não vai torná-la válida.
Já a falha ao gravar a transição, tipicamente o Postgres indisponível, é retentada uma
única vez; insistir em uma mensagem já reentregue transformaria a fila em um laço quente.
Persistindo a falha, a mensagem para em `video-status-dlq`, de onde pode ser reprocessada.
O que ela nunca faz é sumir: descartá-la deixaria o vídeo travado no status anterior para
sempre, sem nada indicando que houve perda.
