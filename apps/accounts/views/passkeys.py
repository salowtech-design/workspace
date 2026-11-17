"""
Passkey (WebAuthn / FIDO2) Views.
Implements:
1. Registration options and attestation verification for authenticated users.
2. Authentication options and assertion verification for passwordless / MFA sign-in.
3. Passkey revocation.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.accounts.auth.errors import AuthException
from apps.accounts.auth.passkeys import (
    generate_passkey_authentication_options,
    generate_passkey_registration_options,
    revoke_passkey,
    verify_passkey_authentication,
    verify_passkey_registration,
)
from apps.accounts.auth.sessions import create_verified_session
from apps.accounts.auth.stepup import is_sudo_active
from apps.accounts.models import CustomUser


@login_required
def passkey_register_options_view(request):
    """Generate W3C registration options for enrolling a new passkey."""
    try:
        options_json = generate_passkey_registration_options(request, request.user)
        return JsonResponse(json.loads(options_json))
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
def passkey_register_verify_view(request):
    """Verify browser WebAuthn attestation response and persist credential."""
    try:
        body = json.loads(request.body.decode("utf-8"))
        credential_data = body.get("credential")
        display_name = body.get("displayName", "My Passkey")

        if not credential_data:
            return JsonResponse({"error": "Missing credential payload"}, status=400)

        passkey = verify_passkey_registration(
            request=request,
            user=request.user,
            credential_data=credential_data,
            display_name=display_name,
        )
        return JsonResponse(
            {
                "success": True,
                "passkey_id": passkey.id,
                "display_name": passkey.display_name,
            }
        )
    except AuthException as e:
        return JsonResponse({"error": e.user_message}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Registration verification failed: {e}"}, status=400)


def passkey_auth_options_view(request):
    """Generate W3C authentication options for passkey sign-in."""
    try:
        emp_id = request.GET.get("employee_id", "").strip().upper()
        user = None
        if emp_id:
            user = CustomUser.objects.filter(employee_id=emp_id, is_active=True).first()

        options_json = generate_passkey_authentication_options(request, user=user)
        return JsonResponse(json.loads(options_json))
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def passkey_auth_verify_view(request):
    """Verify browser WebAuthn assertion and establish authenticated session."""
    try:
        body = json.loads(request.body.decode("utf-8"))
        credential_data = body.get("credential")
        emp_id = body.get("employee_id", "").strip().upper()

        if not credential_data:
            return JsonResponse({"error": "Missing assertion payload"}, status=400)

        user = None
        if emp_id:
            user = CustomUser.objects.filter(employee_id=emp_id, is_active=True).first()

        authenticated_user = verify_passkey_authentication(
            request=request,
            credential_data=credential_data,
            user=user,
        )

        create_verified_session(request, authenticated_user)
        return JsonResponse({"success": True, "redirect_url": "/attendance/"})
    except AuthException as e:
        return JsonResponse({"error": e.user_message}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"Authentication assertion failed: {e}"}, status=400)


@login_required
@require_POST
def passkey_delete_view(request, passkey_id: int):
    """
    Revoke a registered passkey.
    Enforces re-authentication (sudo mode) before credential removal.
    """
    if not is_sudo_active(request):
        if request.headers.get(
            "x-requested-with"
        ) == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
            return JsonResponse(
                {
                    "error": "Step-up authentication required: please re-authenticate.",
                    "sudo_required": True,
                },
                status=403,
            )
        messages.warning(
            request, "Recent password verification required before removing credentials."
        )
        return redirect("/auth/security/sudo/?next=/auth/security/")

    success = revoke_passkey(request.user, passkey_id)
    if success:
        messages.success(request, "Passkey credential has been revoked.")
    else:
        messages.error(request, "Passkey could not be found or revoked.")
    return redirect("accounts:security")
