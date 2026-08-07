"""Testes do acesso ao object storage, com o boto3 substituído por um dublê."""
import io

import pytest

from app import config, storage


class FakeS3:
    def __init__(self):
        self.enviados = []
        self.assinados = []

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):  # noqa: N803 (assinatura do boto3)
        self.enviados.append((bucket, key, fileobj.read(), ExtraArgs))

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):  # noqa: N803
        self.assinados.append((operation, Params, ExpiresIn))
        return f"{config.S3_ENDPOINT_URL}/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


@pytest.fixture
def s3(monkeypatch):
    duble = FakeS3()
    storage.reset()
    monkeypatch.setattr(storage, "client", lambda: duble)
    yield duble
    storage.reset()


@pytest.mark.asyncio
async def test_upload_envia_para_o_bucket_com_o_content_type(s3):
    await storage.upload(io.BytesIO(b"conteudo"), "raw/ana/video.mp4", "video/mp4")

    bucket, key, corpo, extras = s3.enviados[0]
    assert (bucket, key, corpo) == (config.S3_BUCKET, "raw/ana/video.mp4", b"conteudo")
    assert extras == {"ContentType": "video/mp4"}


@pytest.mark.asyncio
async def test_url_assinada_pede_get_object_com_o_ttl(s3):
    await storage.presigned_url("processed/ana/pronto.zip", 900)

    operacao, params, expira = s3.assinados[0]
    assert operacao == "get_object"
    assert params == {"Bucket": config.S3_BUCKET, "Key": "processed/ana/pronto.zip"}
    assert expira == 900


@pytest.mark.asyncio
async def test_url_assinada_troca_o_endereco_interno_pelo_publico(s3, monkeypatch):
    # Dentro do Compose o serviço fala com "minio:9000", nome que só resolve na rede do
    # Docker. A URL entregue ao navegador precisa apontar para o endereço externo, senão
    # o download quebra fora do cluster.
    monkeypatch.setattr(config, "S3_PUBLIC_ENDPOINT_URL", "https://fiapx.example.com")

    url = await storage.presigned_url("processed/ana/pronto.zip", 900)

    assert url.startswith("https://fiapx.example.com/")
    assert config.S3_ENDPOINT_URL not in url


def test_o_cliente_e_criado_uma_unica_vez(monkeypatch):
    criados = []

    def fake_boto_client(*args, **kwargs):
        criados.append(kwargs)
        return object()

    storage.reset()
    monkeypatch.setattr(storage.boto3, "client", fake_boto_client)

    primeiro, segundo = storage.client(), storage.client()

    assert primeiro is segundo
    assert len(criados) == 1
    assert criados[0]["endpoint_url"] == config.S3_ENDPOINT_URL
    storage.reset()
