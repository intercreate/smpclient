"""`smpclient` module exceptions."""


class SMPClientException(Exception): ...


class SMPBadSequence(SMPClientException): ...


class SMPUploadError(SMPClientException): ...


class SMPValidationException(SMPClientException):
    def __init__(self, msg: str, details: str) -> None:
        self.msg: str = msg
        self.details: str = details
        super().__init__(msg)
