# fiapx-video-management-service

FastAPI para upload, status paginado, download de ZIP e WebSocket. A documentação interativa é exposta em `/docs`.

## Organização

| Módulo | Responsabilidade |
| :--- | :--- |
| `app/config.py` | Configuração lida do ambiente |
| `app/models.py` | Mapeamento da tabela `videos` |
| `app/db.py` | Engine e sessão assíncrona |
| `app/security.py` | Introspecção do Bearer token no auth-service |
| `app/storage.py` | Envio e URL assinada no MinIO/S3 |
| `app/messaging.py` | Conexão única com o RabbitMQ: publicação e consumo |
| `app/events.py` | Envelope dos eventos, conforme `contracts/video-event.schema.json` |
| `app/cache.py` | Cache de introspecção e distribuição de atualizações entre réplicas |
| `app/hub.py` | Conexões WebSocket abertas por usuário |
| `app/metrics.py` | Métricas expostas em `/metrics` |
| `app/main.py` | Rotas HTTP e ciclo de vida da aplicação |

## Papel do Redis

Só uma réplica consome cada evento da fila, mas o usuário pode estar conectado ao
WebSocket de qualquer outra. O Redis reparte a atualização entre todas por pub/sub, e
guarda as claims já validadas por `TOKEN_CACHE_TTL_SECONDS` para tirar a introspecção do
caminho crítico. Se ele estiver fora, a API continua respondendo: a introspecção volta a
bater no auth-service a cada requisição e o alcance do WebSocket fica restrito à réplica.

## Testes

```sh
pip install -r requirements-dev.txt
pytest
```

A suíte roda sobre SQLite temporário, sem depender de PostgreSQL, RabbitMQ ou MinIO.

## Contrato de eventos

`contracts/video-event.schema.json` é uma cópia do contrato canônico mantido em `fiapx-platform`.
`tests/test_events.py` valida contra ele os eventos publicados e, quando o repositório da
plataforma está presente no checkout, confere se as duas cópias continuam idênticas.
