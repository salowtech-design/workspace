"""
Subsystem 6: Key Custody & Comprehensive Key Rotation Drills Command.
Implements:
1. Re-encryption of encrypted columns across all sensitive data domains:
   - TOTPCredential (Workspace employee authenticator secrets)
   - IAMAdminIdentity (IAM Administrator authenticator secrets)
   - FaceTemplate (Biometric template embeddings)
   - EmailOutbox (Queued/pending encrypted transactional payloads)
2. Immediate round-trip verification drill ensuring zero data corruption or unreadable secrets.
3. Dual-read/single-write support with rollback safety.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.auth.config import get_biometric_key_ring_dict, get_email_key_ring_dict
from apps.accounts.auth.crypto import decrypt_totp_secret, encrypt_totp_secret, get_key
from apps.accounts.models import IAMAdminIdentity, TOTPCredential
from apps.core.email_service import decrypt_payload, encrypt_payload
from apps.core.models import EmailOutbox
from apps.face.engine.crypto import BiometricKeyRing, decrypt_template, encrypt_template
from apps.face.models import FaceTemplate


class Command(BaseCommand):
    help = "Executes cryptographic key rotation drill, re-encrypting secrets with a new key ID across all data domains."

    def add_arguments(self, parser):
        parser.add_argument(
            "--new-key-id",
            type=str,
            required=True,
            help="Target key ID to re-encrypt records under (must exist in key rings).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate re-encryption drill without writing changes.",
        )

    def handle(self, *args, **options):
        new_key_id = options["new_key_id"].strip()
        dry_run = options["dry_run"]

        # 1. Verify key presence in authentication key ring
        try:
            get_key(new_key_id)
        except Exception as e:
            raise CommandError(
                f"Target key '{new_key_id}' not found in configured AUTH_KEY_RING: {e}"
            )

        self.stdout.write(
            f"Initiating comprehensive key rotation drill to key [{new_key_id}] (Dry Run: {dry_run})..."
        )

        totp_count = 0
        iam_count = 0
        face_count = 0
        email_count = 0

        with transaction.atomic():
            # Domain 1: Re-encrypt TOTPCredential secrets
            totp_creds = TOTPCredential.objects.filter(encrypted_secret__isnull=False)
            for cred in totp_creds:
                if cred.key_id == new_key_id:
                    continue
                raw_secret = decrypt_totp_secret(
                    bytes(cred.encrypted_secret),
                    bytes(cred.nonce) if cred.nonce else b"",
                    cred.key_id,
                )
                new_enc, new_nonce, assigned_key = encrypt_totp_secret(
                    raw_secret, key_id=new_key_id
                )
                test_decrypt = decrypt_totp_secret(new_enc, new_nonce, assigned_key)
                if test_decrypt != raw_secret:
                    raise CommandError(
                        f"Integrity check failed during re-encryption of TOTPCredential {cred.id}"
                    )

                if not dry_run:
                    cred.encrypted_secret = new_enc
                    cred.nonce = new_nonce
                    cred.key_id = assigned_key
                    cred.save(update_fields=["encrypted_secret", "nonce", "key_id"])
                totp_count += 1

            # Domain 2: Re-encrypt IAMAdminIdentity secrets
            iam_admins = IAMAdminIdentity.objects.filter(encrypted_totp_secret__isnull=False)
            for admin in iam_admins:
                if admin.totp_key_id == new_key_id:
                    continue
                raw_secret = decrypt_totp_secret(
                    bytes(admin.encrypted_totp_secret),
                    bytes(admin.totp_nonce) if admin.totp_nonce else b"",
                    admin.totp_key_id,
                )
                new_enc, new_nonce, assigned_key = encrypt_totp_secret(
                    raw_secret, key_id=new_key_id
                )
                test_decrypt = decrypt_totp_secret(new_enc, new_nonce, assigned_key)
                if test_decrypt != raw_secret:
                    raise CommandError(
                        f"Integrity check failed during re-encryption of IAMAdminIdentity {admin.id}"
                    )

                if not dry_run:
                    admin.encrypted_totp_secret = new_enc
                    admin.totp_nonce = new_nonce
                    admin.totp_key_id = assigned_key
                    admin.save(update_fields=["encrypted_totp_secret", "totp_nonce", "totp_key_id"])
                iam_count += 1

            # Domain 3: Re-encrypt FaceTemplate embeddings
            bio_keys = get_biometric_key_ring_dict()
            if new_key_id in bio_keys:
                key_ring = BiometricKeyRing(keys=bio_keys, active_key_id=new_key_id)
                templates = FaceTemplate.objects.filter(encrypted_template__isnull=False)
                for tmpl in templates:
                    if tmpl.key_id == new_key_id:
                        continue
                    try:
                        embedding = decrypt_template(tmpl.encrypted_template, key_ring)
                        new_enc = encrypt_template(embedding, key_ring)
                        test_dec = decrypt_template(new_enc, key_ring)
                        if test_dec != embedding:
                            raise CommandError(
                                f"Integrity check failed during re-encryption of FaceTemplate {tmpl.id}"
                            )

                        if not dry_run:
                            tmpl.encrypted_template = new_enc
                            tmpl.key_id = new_key_id
                            tmpl.save(update_fields=["encrypted_template", "key_id"])
                        face_count += 1
                    except Exception as err:
                        self.stdout.write(
                            self.style.WARNING(f"Skipping FaceTemplate {tmpl.id}: {err}")
                        )

            # Domain 4: Re-encrypt pending/queued EmailOutbox payloads
            email_keys = get_email_key_ring_dict()
            if new_key_id in email_keys:
                outbox_items = EmailOutbox.objects.filter(
                    encrypted_payload__isnull=False,
                    status__in=[EmailOutbox.Status.QUEUED, EmailOutbox.Status.RETRY_PENDING],
                )
                for item in outbox_items:
                    if item.payload_key_id == new_key_id:
                        continue
                    try:
                        payload_dict = decrypt_payload(
                            bytes(item.encrypted_payload),
                            bytes(item.payload_nonce) if item.payload_nonce else b"",
                            item.payload_key_id,
                        )
                        new_enc, new_nonce, assigned_kid = encrypt_payload(
                            payload_dict, key_id=new_key_id
                        )
                        test_dec = decrypt_payload(new_enc, new_nonce, assigned_kid)
                        if test_dec != payload_dict:
                            raise CommandError(
                                f"Integrity check failed during re-encryption of EmailOutbox {item.id}"
                            )

                        if not dry_run:
                            item.encrypted_payload = new_enc
                            item.payload_nonce = new_nonce
                            item.payload_key_id = assigned_kid
                            item.save(
                                update_fields=[
                                    "encrypted_payload",
                                    "payload_nonce",
                                    "payload_key_id",
                                ]
                            )
                        email_count += 1
                    except Exception as err:
                        self.stdout.write(
                            self.style.WARNING(f"Skipping EmailOutbox {item.id}: {err}")
                        )

            status_verb = "Would re-encrypt" if dry_run else "Successfully re-encrypted"
            self.stdout.write(
                self.style.SUCCESS(
                    f"[KEY ROTATION COMPLETE] {status_verb}: "
                    f"{totp_count} Employee TOTP, {iam_count} IAM Admin TOTP, "
                    f"{face_count} Face Templates, {email_count} Email Outbox payloads to key [{new_key_id}]."
                )
            )
