from smp import header as smpheader
from smp import image_management as smpimg

from smpclient.generics import SMPError


class ImageManagementError(SMPError[smpimg.IMG_MGMT_ERR]):
    _GROUP_ID = smpheader.GroupId.IMAGE_MANAGEMENT


class _ImageGroupBase:
    _ErrorV0 = smpimg.ImageManagementErrorV0
    _ErrorV1 = smpimg.ImageManagementErrorV1
    _Error = ImageManagementError


class ImageStatesRead(smpimg.ImageStatesReadRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesReadResponse


class ImageStatesWrite(smpimg.ImageStatesWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageStatesWriteResponse


class ImageUploadWrite(smpimg.ImageUploadWriteRequest, _ImageGroupBase):
    _Response = smpimg.ImageUploadProgressWriteResponse


class ImageErase(smpimg.ImageEraseRequest, _ImageGroupBase):
    _Response = smpimg.ImageEraseResponse
