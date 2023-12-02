from typing import ClassVar

from smp import header as smpheader
from smp import os_management as smpos

from smpclient.generics import SMPError


class OSManagementError(SMPError):  # TODO: need defs in dependency
    _GROUP_ID = smpheader.GroupId.IMAGE_MANAGEMENT


class _OSGroupBase:
    ErrorV0 = OSManagementError  # TODO: need defs in dependency
    ErrorV1 = OSManagementError  # TODO: need defs in dependency
    Error = OSManagementError  # TODO: need defs in dependency


class EchoWrite(smpos.EchoWriteRequest, _OSGroupBase):
    Response: ClassVar = smpos.EchoWriteResponse


class ResetWrite(smpos.ResetWriteRequest, _OSGroupBase):
    Response: ClassVar = smpos.ResetWriteResponse
