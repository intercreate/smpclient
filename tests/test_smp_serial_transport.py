"""Tests for `SMPSerialTransport`."""


from smpclient.transport.serial import SMPSerialTransport


def test_constructor() -> None:
    SMPSerialTransport()
