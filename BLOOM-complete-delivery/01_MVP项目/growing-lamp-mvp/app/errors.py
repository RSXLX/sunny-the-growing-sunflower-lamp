"""Errors shared by application modules and HTTP handlers."""


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message
