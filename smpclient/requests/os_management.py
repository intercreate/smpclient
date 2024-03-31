from smp import header as smpheader
from smp import os_management as smpos

from smpclient.generics import SMPError


class OSManagementError(SMPError[smpos.OS_MGMT_RET_RC]):
    _GROUP_ID = smpheader.GroupId.OS_MANAGEMENT


class _OSGroupBase:
    _ErrorV0 = smpos.OSManagementErrorV0
    _ErrorV1 = smpos.OSManagementErrorV1
    _Error = OSManagementError


class EchoWrite(smpos.EchoWriteRequest, _OSGroupBase):
    _Response = smpos.EchoWriteResponse


class ResetWrite(smpos.ResetWriteRequest, _OSGroupBase):
    _Response = smpos.ResetWriteResponse
