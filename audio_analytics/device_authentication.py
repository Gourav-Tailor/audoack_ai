from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Device


class DeviceAuthentication(BaseAuthentication):
    """
    Authenticate API requests using Device.key.

    Client sends:

        Authorization: Token <device_key>
    """

    keyword = "Token"

    def authenticate(self, request):
        auth = request.headers.get("Authorization")

        if not auth:
            return None

        try:
            keyword, key = auth.split(" ", 1)
        except ValueError:
            raise AuthenticationFailed("Invalid Authorization header.")

        if keyword.lower() != self.keyword.lower():
            raise AuthenticationFailed("Invalid authentication scheme.")

        key = key.strip()

        if not key:
            raise AuthenticationFailed("Device token is missing.")

        try:
            device = Device.objects.select_related("user").get(key=key)
        except Device.DoesNotExist:
            raise AuthenticationFailed("Invalid device token.")

        if not device.user:
            raise AuthenticationFailed("Device has no associated user.")

        return (device.user, device)

    def authenticate_header(self, request):
        return self.keyword
