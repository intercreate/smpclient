from typing import ClassVar

from smp import header as smpheader
from smp import os_management as smpos

from smpclient.generics import SMPError


class OSManagementError(SMPError[smpos.OS_MGMT_RET_RC]):
    _GROUP_ID = smpheader.GroupId.OS_MANAGEMENT


class _OSGroupBase:
    ErrorV0 = smpos.OSManagementErrorV0
    ErrorV1 = smpos.OSManagementErrorV1
    Error = OSManagementError


class EchoWrite(smpos.EchoWriteRequest, _OSGroupBase):
    Response: ClassVar = smpos.EchoWriteResponse


class ResetWrite(smpos.ResetWriteRequest, _OSGroupBase):
    Response: ClassVar = smpos.ResetWriteResponse
