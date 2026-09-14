from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import MobileAuthToken


class MobileBearerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")

        if scheme.lower() != "bearer" or not token:
            return None

        auth_token = (
            MobileAuthToken.objects.select_related("user")
            .filter(token=token.strip(), revoked=False)
            .first()
        )
        if auth_token is None:
            raise AuthenticationFailed("Invalid or expired access token.")

        return (auth_token.user, auth_token)

    def authenticate_header(self, request):
        return "Bearer"
