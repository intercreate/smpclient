from smp import header as smphdr
from smp.user import intercreate as smpic

from smpclient.generics import SMPError


class Error(SMPError[smpic.IC_MGMT_ERR]):
    _GROUP_ID = smphdr.UserGroupId.INTERCREATE


class _GroupBase:
    _ErrorV0 = smpic.ErrorV0
    _ErrorV1 = smpic.ErrorV1
    _Error = Error


class ImageUploadWrite(smpic.ImageUploadWriteRequest, _GroupBase):
    _Response = smpic.ImageUploadWriteResponse
