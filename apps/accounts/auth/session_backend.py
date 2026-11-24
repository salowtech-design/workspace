"""
Protected Database Session Backend for Salow Workspace.
Guarantees that raw session cookie bearer values are never stored in the database.
All database lookups and storage use keyed HMAC-SHA256 verifiers/digests.
"""

import hmac
import logging
from typing import Optional

from django.contrib.sessions.backends.db import SessionStore as DBSessionStore
from django.core.exceptions import SuspiciousOperation
from django.utils import timezone

from apps.accounts.auth.config import get_session_hmac_key

logger = logging.getLogger(__name__)


def hash_session_bearer(raw_session_key: str) -> str:
    """
    Computes a keyed HMAC-SHA256 digest of the raw session cookie bearer token.
    Returns 40-character hexadecimal digest, matching Django's varchar(40) session_key column.
    """
    if not raw_session_key:
        return ""
    key = get_session_hmac_key()
    return hmac.new(key, str(raw_session_key).encode("utf-8"), "sha256").hexdigest()[:40]


class SessionStore(DBSessionStore):
    """
    Custom Django session store that hashes session keys before querying or writing to the DB.
    The client cookie retains the high-entropy raw bearer value, while django_session and
    UserSession store only the one-way keyed cryptographic digest.
    """

    def _get_db_session_key(self, session_key: Optional[str] = None) -> str:
        raw_key = session_key or self.session_key
        if not raw_key:
            return ""
        return hash_session_bearer(raw_key)

    def load(self):
        """
        Load session data from database by looking up the keyed digest of the incoming cookie.
        Strictly prohibits unhashed raw session lookups.
        """
        if not self.session_key:
            self._session_key = None
            return {}

        db_key = self._get_db_session_key()
        try:
            s = self.model.objects.get(
                session_key=db_key,
                expire_date__gt=timezone.now(),
            )
            return self.decode(s.session_data)
        except (self.model.DoesNotExist, SuspiciousOperation):
            self._session_key = None
            return {}

    def exists(self, session_key):
        if not session_key:
            return False
        db_key = self._get_db_session_key(session_key)
        return self.model.objects.filter(session_key=db_key).exists()

    def create_model_instance(self, data):
        """Create a new session model instance using the keyed digest."""
        db_key = self._get_db_session_key()
        return self.model(
            session_key=db_key,
            session_data=self.encode(data),
            expire_date=self.get_expiry_date(),
        )

    def delete(self, session_key=None):
        if session_key is None:
            if self.session_key is None:
                return
            session_key = self.session_key
        db_key = self._get_db_session_key(session_key)
        self.model.objects.filter(session_key=db_key).delete()
