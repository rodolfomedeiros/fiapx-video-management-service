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
| `app/hub.py` | Conexões WebSocket abertas por usuário |
| `app/main.py` | Rotas HTTP e ciclo de vida da aplicação |

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
