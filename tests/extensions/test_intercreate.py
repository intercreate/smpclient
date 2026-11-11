"""Test the Intercreate extensions."""

from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from smp import header as smphdr
from smp import packet as smppacket
from smp.user import intercreate as smpic

from smpclient.extensions.intercreate import ICUploadClient
from smpclient.requests.user import intercreate as ic
from smpclient.transport.serial import SMPSerialTransport
from tests.test_smp_client import SMPMockTransport


@patch('tests.test_smp_client.SMPSerialTransport.mtu', new_callable=PropertyMock)
@pytest.mark.asyncio
async def test_upload_hello_world_bin_encoded(mock_mtu: PropertyMock) -> None:
    mock_mtu.return_value = 127  # testing at 127, the default for Shell Transport

    with open(
        str(Path("tests", "fixtures", "zephyr-v3.5.0-2795-g28ff83515d", "hello_world.signed.bin")),
        'rb',
    ) as f:
        image = f.read()

    m = SMPSerialTransport()
    s = ICUploadClient(m, "address")
    assert s._transport.mtu == 127
    assert s._transport.max_unencoded_size < 127

    packets: list[bytes] = []

    def mock_write(data: bytes) -> int:
        """Accumulate the raw packets in the global `packets`."""
        assert len(data) <= s._transport.mtu
        packets.append(data)
        return len(data)

    s._transport._conn.write = mock_write  # type: ignore
    type(s._transport._conn).out_waiting = 0  # type: ignore

    async def mock_request(request: ic.ImageUploadWrite) -> smpic.ImageUploadWriteResponse:
        # call the real send method (with write mocked) but don't bother with receive
        # this does provide coverage for the MTU-limited encoding done in the send method
        await s._transport.send(request.BYTES)
        return ic.ImageUploadWrite._Response.get_default()(off=request.off + len(request.data))  # type: ignore # noqa

    s.request = mock_request  # type: ignore

    assert s._transport.max_unencoded_size < s._transport.mtu, (
        "The serial transport has encoding overhead"
    )

    async for _ in s.ic_upload(image):
        pass

    reconstructed_image = bytearray([])

    decoder = smppacket.decode()
    next(decoder)

    for packet in packets:
        try:
            decoder.send(packet)
        except StopIteration as e:
            reconstructed_request = smpic.ImageUploadWriteRequest.loads(e.value)
            reconstructed_image.extend(reconstructed_request.data)

            decoder = smppacket.decode()
            next(decoder)

    assert reconstructed_image == image


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [smphdr.Version.V1, smphdr.Version.V2])
async def test_ic_upload_uses_the_requested_smp_version(version: smphdr.Version) -> None:
    """Every chunk that `ic_upload()` sends carries the requested SMP version."""
    m = SMPMockTransport()
    m._mtu = 498
    m._max_unencoded_size = 498
    s = ICUploadClient(m, "address", 2.5)

    data = bytes([i % 255 for i in range(4097)])
    sent: list[bytes] = []

    async def send(frame: bytes) -> None:
        sent.append(frame)

    async def receive() -> bytes:
        request = ic.ImageUploadWrite.loads(sent[-1])
        return smpic.ImageUploadWriteResponse(
            sequence=request.header.sequence, off=request.off + len(request.data)
        ).BYTES

    m.send = send  # type: ignore[assignment]
    m.receive = receive  # type: ignore[assignment]

    async for _ in s.ic_upload(data, version=version):
        pass

    assert len(sent) > 1, "the data should not fit in a single chunk"
    assert {smphdr.Header.loads(frame[: smphdr.Header.SIZE]).version for frame in sent} == {version}
