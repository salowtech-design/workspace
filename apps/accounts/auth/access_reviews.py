"""
Subsystem 8: Quarterly Access Reviews & Dormant Account Detection Engine.
Implements:
1. Automated quarterly campaign generation (AccessReviewCampaign).
2. Inactivity analysis flagging accounts inactive for >= 60 days.
3. Exited employee access detection.
4. Administrative certification and revocation decision workflows.
"""

from datetime import timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from apps.accounts.auth.audit import log_auth_event
from apps.accounts.models import AccessReviewCampaign, AccessReviewItem, CustomUser, UserSession

INACTIVITY_THRESHOLD_DAYS = 60


def generate_quarterly_campaign(
    quarter_name: Optional[str] = None,
    reviewer: Optional[CustomUser] = None,
) -> Tuple[AccessReviewCampaign, int, int]:
    """
    Creates or resumes an access review campaign for the quarter.
    Populates review items for all users, automatically identifying:
    - Dormant accounts (inactivity >= 60 days)
    - Exited employees with lingering access
    Returns (campaign, total_items, dormant_items).
    """
    now = timezone.now()
    if not quarter_name:
        q_num = (now.month - 1) // 3 + 1
        quarter_name = f"{now.year}-Q{q_num}"

    with transaction.atomic():
        campaign, created = AccessReviewCampaign.objects.get_or_create(
            quarter=quarter_name,
            defaults={
                "name": f"Quarterly User Access Review — {quarter_name}",
                "reviewer": reviewer,
                "status": AccessReviewCampaign.Status.ACTIVE,
            },
        )

        dormant_cutoff = now - timedelta(days=INACTIVITY_THRESHOLD_DAYS)
        users = CustomUser.objects.all()

        total_items = 0
        dormant_items = 0

        for user in users:
            # Determine if user is dormant
            is_dormant = False
            reason = ""

            if user.status == CustomUser.Status.EXITED or not user.is_active:
                is_dormant = True
                reason = "Exited or deactivated employee record flagged for review."
            elif user.last_login:
                if user.last_login < dormant_cutoff:
                    is_dormant = True
                    reason = f"Dormant account: no authentication activity since {user.last_login.strftime('%Y-%m-%d')} (>= {INACTIVITY_THRESHOLD_DAYS} days)."
            elif hasattr(user, "date_joined") and user.date_joined < dormant_cutoff and user.status == CustomUser.Status.INVITED:
                is_dormant = True
                reason = f"Unactivated invite older than {INACTIVITY_THRESHOLD_DAYS} days."

            decision = (
                AccessReviewItem.Decision.FLAGGED_DORMANT
                if is_dormant
                else AccessReviewItem.Decision.PENDING
            )

            item, item_created = AccessReviewItem.objects.get_or_create(
                campaign=campaign,
                user=user,
                defaults={
                    "decision": decision,
                    "reason": reason,
                },
            )

            total_items += 1
            if is_dormant:
                dormant_items += 1

        log_auth_event(
            event="access_review_campaign_generated",
            code="INFO",
            step="access_review",
            employee_id="SYSTEM",
            reference_id=quarter_name,
            message=f"Campaign [{quarter_name}] created: {total_items} accounts ({dormant_items} flagged dormant)",
        )

    return campaign, total_items, dormant_items
