from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from prefect import settings
from prefect.server import models
from prefect.server.database.interface import PrefectDBInterface
from prefect.server.schemas import core


@pytest.fixture
async def csrf_token(session: AsyncSession) -> core.CsrfToken:
    token = await models.csrf_token.create_or_update_csrf_token(
        session=session, client="client123"
    )
    return token


class TestCreateOrUpdateCsrfToken:
    async def test_can_create_csrf_token(self, session: AsyncSession):
        with mock.patch("secrets.token_hex", return_value="token123"):
            token = await models.csrf_token.create_or_update_csrf_token(
                session=session, client="client123"
            )

        now = datetime.now(timezone.utc)
        fuzzy_expiration_start = now + (
            settings.PREFECT_SERVER_CSRF_TOKEN_EXPIRATION.value()
            - timedelta(seconds=10)
        )
        fuzzy_expiration_end = now + (
            settings.PREFECT_SERVER_CSRF_TOKEN_EXPIRATION.value()
            + timedelta(seconds=10)
        )

        assert token.client == "client123"
        assert token.token == "token123"
        assert fuzzy_expiration_start < token.expiration < fuzzy_expiration_end

    async def test_can_update_csrf_token(self, session: AsyncSession):
        # Create a token
        with mock.patch("secrets.token_hex", return_value="token123"):
            token = await models.csrf_token.create_or_update_csrf_token(
                session=session, client="client123"
            )

        # Now update it
        with mock.patch("secrets.token_hex", return_value="token456"):
            updated = await models.csrf_token.create_or_update_csrf_token(
                session=session, client="client123"
            )

        now = datetime.now(timezone.utc)
        fuzzy_expiration_start = now + (
            settings.PREFECT_SERVER_CSRF_TOKEN_EXPIRATION.value()
            - timedelta(seconds=10)
        )
        fuzzy_expiration_end = now + (
            settings.PREFECT_SERVER_CSRF_TOKEN_EXPIRATION.value()
            + timedelta(seconds=10)
        )

        assert updated.client == "client123"
        assert updated.token == "token456"
        assert updated.expiration > token.expiration
        assert fuzzy_expiration_start < updated.expiration < fuzzy_expiration_end


class TestTokenForClient:
    async def test_can_get_token_for_client(
        self, session: AsyncSession, csrf_token: core.CsrfToken
    ):
        token = await models.csrf_token.read_token_for_client(
            session=session, client="client123"
        )

        assert token
        assert token.client == csrf_token.client
        assert token.token == csrf_token.token
        assert token.expiration == csrf_token.expiration

    async def test_none_no_token_for_client(
        self, session: AsyncSession, csrf_token: core.CsrfToken
    ):
        token = await models.csrf_token.read_token_for_client(
            session=session, client="unknown-client"
        )
        assert token is None

    async def test_none_expired_token(
        self, db: PrefectDBInterface, session: AsyncSession, csrf_token: core.CsrfToken
    ):
        await session.execute(
            sa.update(db.CsrfToken)
            .where(db.CsrfToken.client == csrf_token.client)
            .values(expiration=datetime.now(timezone.utc) - timedelta(days=1))
        )

        token = await models.csrf_token.read_token_for_client(
            session=session, client=csrf_token.client
        )
        assert token is None


class TestDeleteExpiredTokens:
    async def test_can_delete_expired_tokens(
        self, db: PrefectDBInterface, session: AsyncSession
    ):
        # Create some tokens
        for i in range(5):
            await models.csrf_token.create_or_update_csrf_token(
                session=session, client=f"client{i}"
            )

        # Update some of them to be expired
        await session.execute(
            sa.update(db.CsrfToken)
            .where(db.CsrfToken.client.in_(["client0", "client1"]))
            .values(expiration=datetime.now(timezone.utc) - timedelta(days=1))
        )

        await models.csrf_token.delete_expired_tokens(session=session)

        removed = [
            await models.csrf_token.read_token_for_client(
                session=session, client=f"client{i}"
            )
            for i in range(2)
        ]

        existing = [
            await models.csrf_token.read_token_for_client(
                session=session, client=f"client{i}"
            )
            for i in range(2, 5)
        ]

        assert removed == [None, None]
        assert all(token is not None for token in existing)
