"""Testes do destino dado a cada mensagem da fila de status."""
import json

import pytest

from app.messaging import dispatch


class FakeMessage:
    """Mensagem do broker reduzida ao que `dispatch` usa."""

    def __init__(self, body: bytes, redelivered: bool = False):
        self.body = body
        self.redelivered = redelivered
        self.confirmada = False
        self.rejeitada: bool | None = None  # requeue escolhido; None se nunca rejeitada

    async def ack(self):
        self.confirmada = True

    async def reject(self, requeue: bool):
        self.rejeitada = requeue


def evento(**campos):
    return json.dumps({"video_id": "abc", "status": "COMPLETED", **campos}).encode()


async def aplica(payload):
    pass


async def banco_fora_do_ar(payload):
    raise RuntimeError("connection refused")


@pytest.mark.asyncio
async def test_evento_aplicado_e_confirmado():
    mensagem = FakeMessage(evento())

    await dispatch(mensagem, aplica)

    assert (mensagem.confirmada, mensagem.rejeitada) == (True, None)


@pytest.mark.asyncio
async def test_evento_ilegivel_vai_direto_para_a_dlq():
    mensagem = FakeMessage(b"{ isto nao e json")

    await dispatch(mensagem, aplica)

    assert (mensagem.confirmada, mensagem.rejeitada) == (False, False)


@pytest.mark.asyncio
async def test_falha_do_handler_devolve_a_mensagem_para_uma_nova_tentativa():
    mensagem = FakeMessage(evento(), redelivered=False)

    await dispatch(mensagem, banco_fora_do_ar)

    assert mensagem.rejeitada is True


@pytest.mark.asyncio
async def test_falha_na_reentrega_para_na_dlq_em_vez_de_sumir():
    mensagem = FakeMessage(evento(), redelivered=True)

    await dispatch(mensagem, banco_fora_do_ar)

    assert (mensagem.confirmada, mensagem.rejeitada) == (False, False)
