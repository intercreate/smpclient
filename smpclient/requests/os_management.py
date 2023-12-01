from smp import header as smpheader
from smp import os_management as smpos

from smpclient.generics import SMPError


class OSManagementError(SMPError):
    _GROUP_ID = smpheader.GroupId.IMAGE_MANAGEMENT


class _OSGroupBase:
    ErrorV0 = OSManagementError  # TODO: need defs in dependency
    ErrorV1 = OSManagementError
    Error = OSManagementError


class EchoWrite(smpos.EchoWriteRequest, _OSGroupBase):
    Response = smpos.EchoWriteResponse


class ResetWrite(smpos.ResetWriteRequest, _OSGroupBase):
    Response = smpos.ResetWriteResponse
