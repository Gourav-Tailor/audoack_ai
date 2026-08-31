# audio_analytics/management/commands/create_device_token.py
"""
Usage:
    python manage.py create_device_token <username> "<device name>"

Example:
    python manage.py create_device_token gourav "Living Room ESP32"
    python manage.py create_device_token gourav "iPhone App"

Each call creates a NEW device with its own unique key -- unlike the
DRF authtoken Token model, there's no one-per-user limit here, so you
can run this as many times as you have devices.

Prints the key the device/app should send as:
    Authorization: Token <key>
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from audio_analytics.models import Device


class Command(BaseCommand):
    help = "Create a new Device with its own API key, for ESP32/Raspberry Pi/mobile clients."

    def add_arguments(self, parser):
        parser.add_argument("username", type=str)
        parser.add_argument("device_name", type=str)

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist:
            raise CommandError(f"No such user: {options['username']}")

        device = Device.objects.create(user=user, name=options["device_name"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Created device '{device.name}' for user '{user.username}'.\n"
                f"API Token: {device.key}\n\n"
                f"Have the device send this header on every request:\n"
                f"  Authorization: Token {device.key}"
            )
        )
