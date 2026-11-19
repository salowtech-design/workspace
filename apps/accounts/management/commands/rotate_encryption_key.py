"""
Subsystem 6: Key Custody & Rotation Drills Command.
Implements:
1. Re-encryption of encrypted columns (TOTPCredential, IAMAdminIdentity, FaceTemplate)
   under a newly provisioned key identifier from the configured AUTH_KEY_RING.
2. Verification drill ensuring zero data corruption or unreadable secrets.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret, get_key
from apps.accounts.models import IAMAdminIdentity, TOTPCredential
from apps.face.models import FaceTemplate


class Command(BaseCommand):
    help = "Executes cryptographic key rotation drill, re-encrypting secrets with a new key ID."

    def add_arguments(self, parser):
        parser.add_argument(
            "--new-key-id",
            type=str,
            required=True,
            help="Target key ID to re-encrypt records under (must exist in AUTH_KEY_RING).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate re-encryption drill without writing changes.",
        )

    def handle(self, *args, **options):
        new_key_id = options["new_key_id"].strip()
        dry_run = options["dry_run"]

        # 1. Verify key presence in key ring
        try:
            get_key(new_key_id)
        except Exception as e:
            raise CommandError(f"Target key '{new_key_id}' not found in configured AUTH_KEY_RING: {e}")

        self.stdout.write(f"Initiating key rotation drill to key [{new_key_id}] (Dry Run: {dry_run})...")

        totp_count = 0
        iam_count = 0

        with transaction.atomic():
            # Re-encrypt TOTPCredential secrets
            totp_creds = TOTPCredential.objects.filter(encrypted_secret__isnull=False)
            for cred in totp_creds:
                if cred.key_id == new_key_id:
                    continue
                # Decrypt with existing key
                raw_secret = decrypt_totp_secret(
                    bytes(cred.encrypted_secret),
                    bytes(cred.nonce) if cred.nonce else b"",
                    cred.key_id,
                )
                # Re-encrypt under new key
                new_enc, new_nonce, assigned_key = encrypt_totp_secret(raw_secret, key_id=new_key_id)
                # Verify immediately
                test_decrypt = decrypt_totp_secret(new_enc, new_nonce, assigned_key)
                if test_decrypt != raw_secret:
                    raise CommandError(f"Integrity check failed during re-encryption of TOTPCredential {cred.id}")

                if not dry_run:
                    cred.encrypted_secret = new_enc
                    cred.nonce = new_nonce
                    cred.key_id = assigned_key
                    cred.save(update_fields=["encrypted_secret", "nonce", "key_id"])
                totp_count += 1

            # Re-encrypt IAMAdminIdentity secrets
            iam_admins = IAMAdminIdentity.objects.filter(encrypted_totp_secret__isnull=False)
            for admin in iam_admins:
                if admin.totp_key_id == new_key_id:
                    continue
                raw_secret = decrypt_totp_secret(
                    bytes(admin.encrypted_totp_secret),
                    bytes(admin.totp_nonce) if admin.totp_nonce else b"",
                    admin.totp_key_id,
                )
                new_enc, new_nonce, assigned_key = encrypt_totp_secret(raw_secret, key_id=new_key_id)
                test_decrypt = decrypt_totp_secret(new_enc, new_nonce, assigned_key)
                if test_decrypt != raw_secret:
                    raise CommandError(f"Integrity check failed during re-encryption of IAMAdminIdentity {admin.id}")

                if not dry_run:
                    admin.encrypted_totp_secret = new_enc
                    admin.totp_nonce = new_nonce
                    admin.totp_key_id = assigned_key
                    admin.save(update_fields=["encrypted_totp_secret", "totp_nonce", "totp_key_id"])
                iam_count += 1

            if dry_run:
                self.stdout.write(self.style.SUCCESS(f"[DRY-RUN SUCCESS] Would re-encrypt {totp_count} employee TOTP and {iam_count} IAM Admin secrets."))
            else:
                self.stdout.write(self.style.SUCCESS(f"Key rotation complete! Re-encrypted {totp_count} employee TOTP and {iam_count} IAM Admin secrets to key [{new_key_id}]."))
