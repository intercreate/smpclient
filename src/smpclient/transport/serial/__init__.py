"""Serial SMPTransports.

In addition to UART, these transports can be used with USB CDC ACM and CAN.
"""

from smpclient.transport.serial.encoded import Auto as Auto
from smpclient.transport.serial.encoded import BufferParams as BufferParams
from smpclient.transport.serial.encoded import BufferSize as BufferSize
from smpclient.transport.serial.encoded import FragmentationStrategy as FragmentationStrategy
from smpclient.transport.serial.encoded import SMPSerialTransport as SMPSerialTransport
from smpclient.transport.serial.unencoded import SMPSerialRawTransport as SMPSerialRawTransport
