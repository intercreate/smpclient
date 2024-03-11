from typing import ClassVar

from smp import header as smpheader
from smp.user import intercreate as smpic

from smpclient.generics import SMPError


class Error(SMPError[smpic.IC_MGMT_ERR]):
    _GROUP_ID = smpheader.GroupId.INTERCREATE


class _GroupBase:
    ErrorV0 = smpic.ErrorV0
    ErrorV1 = smpic.ErrorV1
    Error = Error


class ImageUploadWrite(smpic.ImageUploadWriteRequest, _GroupBase):
    Response: ClassVar = smpic.ImageUploadWriteResponse
