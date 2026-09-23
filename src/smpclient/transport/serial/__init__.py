"""Serial SMPTransports.

In addition to UART, these transports can be used with USB CDC ACM and CAN.
"""

from smpclient.transport.serial.common import SerialOptions as SerialOptions
from smpclient.transport.serial.encoded import BufferParams as BufferParams
from smpclient.transport.serial.encoded import BufferSize as BufferSize
from smpclient.transport.serial.encoded import (
    SerialFragmentationStrategy as SerialFragmentationStrategy,
)
from smpclient.transport.serial.encoded import SMPSerialTransport as SMPSerialTransport
from smpclient.transport.serial.framing import SerialFraming as SerialFraming
from smpclient.transport.serial.framing.cobs import Cobs as Cobs
from smpclient.transport.serial.unencoded import (
    RawSerialFragmentationStrategy as RawSerialFragmentationStrategy,
)
from smpclient.transport.serial.unencoded import SMPSerialRawTransport as SMPSerialRawTransport
