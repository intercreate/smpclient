from smp.user import intercreate as smpic


class _GroupBase:
    _ErrorV1 = smpic.ErrorV1
    _ErrorV2 = smpic.ErrorV2


class ImageUploadWrite(smpic.ImageUploadWriteRequest, _GroupBase):
    _Response = smpic.ImageUploadWriteResponse
