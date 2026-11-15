from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.permissions import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Generates docs/PERMISSIONS.md based on active role-permission matrix."

    def handle(self, *args, **options):
        docs_path = Path(settings.BASE_DIR) / "docs" / "PERMISSIONS.md"

        all_perms = sorted({perm for perms in ROLE_PERMISSIONS.values() for perm in perms})
        roles = list(ROLE_PERMISSIONS.keys())

        lines = [
            "# Salow Workspace — Permission & Access Control Matrix",
            "",
            "This document is automatically generated from `apps/accounts/permissions.py`.",
            "Default-deny access control is strictly enforced across all modules.",
            "",
            f"| Permission Code | {' | '.join(roles)} |",
            f"| :--- | {' | '.join([':---:' for _ in roles])} |",
        ]

        for perm in all_perms:
            row = [f"`{perm}`"]
            for role in roles:
                has_p = perm in ROLE_PERMISSIONS[role]
                row.append("✅ ALLOW" if has_p else "❌ DENY")
            lines.append(f"| {' | '.join(row)} |")

        lines.extend(
            [
                "",
                "## Sudo Mode Requirements",
                "- Password re-entry is required within 15 minutes for sensitive operations:",
                "  - Employee status deactivation or role elevation.",
                "  - Certificate issuing and revocation.",
                "  - Security settings and manual attendance overrides.",
            ]
        )

        docs_path.write_text("\n".join(lines), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Generated {docs_path} successfully."))
