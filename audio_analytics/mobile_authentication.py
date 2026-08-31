# audio_analytics/mobile_authentication.py

from rest_framework.authentication import BaseAuthentication

from .models import MobileAuthToken


class MobileBearerAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")

        scheme, _, token = header.partition(" ")

        if scheme.lower() != "bearer" or not token:
            return None

        auth_token = (
            MobileAuthToken.objects.select_related("user")
            .filter(
                token=token.strip(),
                revoked=False,
            )
            .first()
        )

        if auth_token is None:
            return None

        return (auth_token.user, auth_token)
