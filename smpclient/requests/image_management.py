from typing import ClassVar

from smp import header as smpheader
from smp import image_management as smpimg

from smpclient.generics import SMPError


class ImageManagementError(SMPError[smpimg.IMG_MGMT_ERR]):
    _GROUP_ID = smpheader.GroupId.IMAGE_MANAGEMENT


class _ImageGroupBase:
    ErrorV0 = smpimg.ImageManagementError1
    ErrorV1 = smpimg.ImageManagementError2
    Error = ImageManagementError


class ImageStatesRead(smpimg.ImageStatesReadRequest, _ImageGroupBase):
    Response: ClassVar = smpimg.ImageStatesReadResponse


class ImageStatesWrite(smpimg.ImageStatesWriteRequest, _ImageGroupBase):
    Response: ClassVar = smpimg.ImageStatesWriteResponse


class ImageUploadWrite(smpimg.ImageUploadWriteRequest, _ImageGroupBase):
    Response: ClassVar = smpimg.ImageUploadProgressWriteResponse
