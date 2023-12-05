"""`smpclient` module exceptions."""


class SMPClientException(Exception):
    ...


class SMPBadSequence(SMPClientException):
    ...


class SMPUploadError(SMPClientException):
    ...
