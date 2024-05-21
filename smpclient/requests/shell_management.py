from smp import header as smpheader
from smp import shell_management as smpshell

from smpclient.generics import SMPError


class ShellManagementError(SMPError[smpshell.SHELL_MGMT_RET_RC]):
    _GROUP_ID = smpheader.GroupId.SHELL_MANAGEMENT


class _ShellGroupBase:
    _ErrorV0 = smpshell.ShellManagementErrorV0
    _ErrorV1 = smpshell.ShellManagementErrorV1
    _Error = ShellManagementError


class Execute(smpshell.ExecuteRequest, _ShellGroupBase):
    _Response = smpshell.ExecuteResponse
