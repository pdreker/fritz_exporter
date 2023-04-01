class FritzHttpException(Exception):
    """Base class for all exceptions in this module."""

    pass


class FritzHttpLoginException(FritzHttpException):
    """Exception raised for errors during login.

    Attributes:
        message -- explanation of the error
    """

    def __init__(self, message):
        self.message = message


class FritzHttpConnectionException(FritzHttpException):
    """Exception raised for errors during connection.

    Attributes:
        message -- explanation of the error
    """

    def __init__(self, message):
        self.message = message
