# audio_analytics/device_views.py
"""
Browser-facing views for managing device API tokens. Uses normal Django
session auth (@login_required) -- this is a page a logged-in user visits
in their browser, not part of the token-authenticated device API in
api_v1.py.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Device


@login_required
def device_tokens_view(request):
    if request.method == "POST":
        device_name = request.POST.get("device_name", "").strip()
        if not device_name:
            messages.error(request, "Please enter a name for the device.")
        elif Device.objects.filter(user=request.user, name=device_name).exists():
            messages.error(request, f"You already have a device named '{device_name}'.")
        else:
            device = Device.objects.create(user=request.user, name=device_name)
            messages.success(
                request,
                f"Created '{device.name}'. Copy its token now -- it won't be shown in full again.",
            )
        return redirect("device_tokens")

    devices = Device.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "audio_analytics/device_tokens.html", {"devices": devices})


@login_required
@require_POST
def delete_device_view(request, device_id):
    device = get_object_or_404(Device, id=device_id, user=request.user)
    device_name = device.name
    device.delete()
    messages.success(request, f"Revoked and removed '{device_name}'.")
    return redirect("device_tokens")
