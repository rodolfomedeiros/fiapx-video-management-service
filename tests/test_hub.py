"""Testes do registro de conexões WebSocket."""
import pytest

from app.hub import Hub


class FakeSocket:
    def __init__(self, quebrado=False):
        self.recebidos = []
        self.aceito = False
        self.quebrado = quebrado

    async def accept(self):
        self.aceito = True

    async def send_json(self, payload):
        if self.quebrado:
            raise RuntimeError("conexão fechada")
        self.recebidos.append(payload)


@pytest.mark.asyncio
async def test_entrega_a_todas_as_abas_do_mesmo_usuario():
    hub = Hub()
    primeira, segunda = FakeSocket(), FakeSocket()
    await hub.connect("ana", primeira)
    await hub.connect("ana", segunda)

    await hub.broadcast("ana", {"status": "COMPLETED"})

    assert primeira.aceito and segunda.aceito
    assert primeira.recebidos == segunda.recebidos == [{"status": "COMPLETED"}]


@pytest.mark.asyncio
async def test_nao_entrega_evento_de_um_usuario_a_outro():
    hub = Hub()
    ana, bruno = FakeSocket(), FakeSocket()
    await hub.connect("ana", ana)
    await hub.connect("bruno", bruno)

    await hub.broadcast("ana", {"status": "ERROR"})

    assert bruno.recebidos == []


@pytest.mark.asyncio
async def test_descarta_socket_que_falhou_no_envio():
    hub = Hub()
    vivo, morto = FakeSocket(), FakeSocket(quebrado=True)
    await hub.connect("ana", vivo)
    await hub.connect("ana", morto)

    await hub.broadcast("ana", {"status": "PROCESSING"})

    assert hub.connections["ana"] == {vivo}
    assert vivo.recebidos == [{"status": "PROCESSING"}]


@pytest.mark.asyncio
async def test_remove_a_entrada_do_usuario_quando_a_ultima_aba_fecha():
    hub = Hub()
    socket = FakeSocket()
    await hub.connect("ana", socket)

    hub.disconnect("ana", socket)

    assert "ana" not in hub.connections
    await hub.broadcast("ana", {"status": "COMPLETED"})  # não pode estourar


@pytest.mark.asyncio
async def test_desconectar_socket_desconhecido_nao_quebra():
    Hub().disconnect("ninguem", FakeSocket())
