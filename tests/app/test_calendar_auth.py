import os
import re
import secrets
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

with patch.dict(
    os.environ,
    {
        "USE_VALUT": "0",
        "DATABASE_HOST": "localhost",
        "DATABASE_PORT": "5432",
        "DATABASE_USER": "test",
        "DATABASE_PASSWORD": "test",
        "DATABASE_DB": "test",
        "MAIL_USERNAME": "test",
        "MAIL_PASSWORD": "test",
        "MAIL_SERVER": "localhost",
        "MAIL_PORT": "1025",
        "MAIL_FROM": "test@example.com",
        "MAILS_TO": "test@example.com",
        "SESSION_SECRET_KEY": "test-session-secret-not-for-production-123456",
    },
):
    from src.app.models import Schedule, User
    from src.app.security import hash_password, verify_password
    from src.database import get_db
    from src.main import app
    from scripts import create_user as user_script


class AsyncDatabaseAdapter:
    """Exercise actual SQL against SQLite without external infrastructure."""

    def __init__(self, session):
        self.session = session

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def delete(self, instance):
        self.session.delete(instance)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def add(self, instance):
        self.session.add(instance)

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


class CalendarAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "correct-password-123"
        cls.password_hash = hash_password(cls.password)

    async def asyncSetUp(self):
        # Test real password checks without relying on thread-pool scheduling.
        offload = patch("src.app.auth.asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *args: fn(*args)))
        offload.start()
        self.addCleanup(offload.stop)
        self.engine = create_engine("sqlite://")
        User.__table__.create(self.engine)
        Schedule.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.users = [
            User(username=name, password_hash=self.password_hash, calendar_token=secrets.token_urlsafe(32), section="2")
            for name in ("alice", "bob")
        ]
        self.db.add_all(self.users)
        self.db.add_all(
            [
                Schedule(
                    section=section,
                    date=datetime.now().date(),
                    day_of_week="Monday",
                    group="A",
                    mode="online",
                    hours={},
                )
                for section in ("1", "2")
            ]
        )
        self.db.commit()

        async def override_db():
            yield AsyncDatabaseAdapter(self.db)

        app.dependency_overrides[get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver")
        self.url = "/api/plan_zajec_lekarski_as.ics"

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        self.db.close()
        self.engine.dispose()

    async def login(self, username="alice", password=None, client=None):
        client = client or self.client
        page = await client.get("/login")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        return await client.post(
            "/login",
            data={
                "username": username,
                "password": password or self.password,
                "csrf": csrf,
            },
        )

    async def test_home_requires_session_even_with_valid_calendar_token(self):
        response = await self.client.get("/", params={"token": self.users[0].calendar_token})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_missing_and_unknown_tokens_rejected_before_schedule_query(self):
        with patch("src.app.router.ScheduleSelector") as selector:
            for token in (None, "", "wrong", "ż" * 43, secrets.token_urlsafe(32)):
                response = await self.client.get(self.url, params={} if token is None else {"token": token})
                self.assertEqual(response.status_code, 401)
            selector.assert_not_called()

    async def test_each_user_can_subscribe_without_browser_session(self):
        with patch("src.app.router.ScheduleSelector") as selector:
            selector.return_value.get_by_section = AsyncMock(return_value=[])
            selector.return_value.get_last_modified_by_section = AsyncMock(
                return_value=datetime(2026, 9, 22, tzinfo=timezone.utc)
            )
            for user in self.users:
                for events_type in ("online", "inperson"):
                    response = await self.client.get(
                        self.url,
                        params={
                            "token": user.calendar_token,
                            "section": "2",
                            "events_type": events_type,
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("BEGIN:VCALENDAR", response.text)
                    self.assertTrue(response.headers["content-type"].startswith("text/calendar"))
                    self.assertNotIn(user.calendar_token, response.text)

    async def test_login_links_use_only_logged_in_users_token_and_logout_works(self):
        response = await self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        cookie = response.headers["set-cookie"].lower()
        for attribute in ("httponly", "secure", "samesite=lax"):
            self.assertIn(attribute, cookie)
        page = await self.client.get("/", params={"token": self.users[1].calendar_token})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.text.count("token=" + self.users[0].calendar_token), 2)
        self.assertNotIn(self.users[1].calendar_token, page.text)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        self.assertEqual((await self.client.post("/logout", data={"csrf": csrf})).status_code, 303)
        self.assertEqual((await self.client.get("/")).headers["location"], "/login")

    async def test_wrong_password_and_unknown_user_show_same_error(self):
        responses = [await self.login("alice", "wrong-password"), await self.login("missing", "wrong-password")]
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertIn("Nieprawidłowa nazwa użytkownika lub hasło.", response.text)
        self.assertEqual((await self.client.get("/")).status_code, 303)

    async def test_csrf_is_required_for_login_and_logout(self):
        self.assertEqual(
            (
                await self.client.post(
                    "/login",
                    data={
                        "username": "alice",
                        "password": self.password,
                    },
                )
            ).status_code,
            403,
        )
        await self.login()
        self.assertEqual((await self.client.post("/logout")).status_code, 403)

    async def test_deleted_user_loses_subscription_and_session_access(self):
        await self.login()
        token = self.users[0].calendar_token
        self.db.delete(self.users[0])
        self.db.commit()
        self.assertEqual((await self.client.get("/")).status_code, 303)
        self.assertEqual((await self.client.get(self.url, params={"token": token})).status_code, 401)

    async def test_unique_username_and_token_are_enforced_by_database(self):
        for username, token in (("alice", secrets.token_urlsafe(32)), ("charlie", self.users[0].calendar_token)):
            self.db.add(User(username=username, password_hash=self.password_hash, calendar_token=token))
            with self.assertRaises(IntegrityError):
                self.db.commit()
            self.db.rollback()

    async def test_script_creates_unique_tokens_and_rejects_existing_username(self):
        engine = SimpleNamespace(dispose=AsyncMock())
        with (
            patch.object(user_script, "create_async_engine", return_value=engine),
            patch.object(user_script, "async_sessionmaker", return_value=lambda: AsyncDatabaseAdapter(self.db)),
        ):
            await user_script.create_user("charlie", self.password_hash, "1", is_admin=True)
            await user_script.create_user("diana", self.password_hash, "2")
            with self.assertRaisesRegex(ValueError, "już istnieje"):
                await user_script.create_user("alice", self.password_hash, "1")
        users = self.db.query(User).all()
        self.assertEqual(len(users), 4)
        self.assertEqual(len({user.calendar_token for user in users}), 4)
        self.assertTrue(all(len(user.calendar_token) == 43 for user in users))
        self.assertTrue(all(user.password_hash == self.password_hash for user in users))

    async def admin_login(self):
        self.users[0].is_admin = True
        self.db.commit()
        await self.login()
        page = await self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)

    async def test_admin_routes_require_admin_and_do_not_leak_tokens(self):
        paths = ("/admin", "/admin/users/new", "/admin/users/2/edit", "/admin/users/2/delete")
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 303)
        await self.login()
        for path in paths:
            self.assertEqual((await self.client.get(path)).status_code, 403)
            if path != "/admin":
                self.assertEqual((await self.client.post(path, data={})).status_code, 403)
        self.users[0].is_admin = True
        self.db.commit()
        page = await self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("alice", page.text)
        self.assertIn("bob", page.text)
        for user in self.users:
            self.assertNotIn(user.calendar_token, page.text)
            self.assertNotIn(user.password_hash, page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")

    async def test_admin_create_edit_and_delete_user(self):
        csrf = await self.admin_login()
        new_page = await self.client.get("/admin/users/new")
        self.assertEqual(new_page.status_code, 200)
        response = await self.client.post(
            "/admin/users/new",
            data={
                "username": "charlie",
                "password": self.password,
                "section": "1",
                "csrf": csrf,
            },
        )
        self.assertEqual(response.status_code, 303)
        created = self.db.query(User).filter_by(username="charlie").one()
        self.assertEqual(created.section, "1")
        self.assertFalse(created.is_admin)
        self.assertTrue(verify_password(self.password, created.password_hash))
        token, user_id = created.calendar_token, created.id
        self.assertNotIn(token, [u.calendar_token for u in self.users])
        self.assertEqual((await self.client.get(f"/admin/users/{user_id}/edit")).status_code, 200)
        response = await self.client.post(
            f"/admin/users/{user_id}/edit",
            data={
                "username": "charlie-edited",
                "section": "2",
                "csrf": csrf,
                "is_admin": "true",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.db.refresh(created)
        self.assertEqual(created.username, "charlie-edited")
        self.assertEqual(created.section, "2")
        self.assertTrue(created.is_admin)
        self.assertEqual(created.calendar_token, token)
        self.assertTrue(verify_password(self.password, created.password_hash))
        confirmation = await self.client.get(f"/admin/users/{user_id}/delete")
        self.assertEqual(confirmation.status_code, 200)
        self.assertIsNotNone(self.db.get(User, user_id))
        self.assertEqual(
            (await self.client.post(f"/admin/users/{user_id}/delete", data={"csrf": csrf})).status_code, 303
        )
        self.assertIsNone(self.db.get(User, user_id))
        self.assertEqual((await self.client.get(self.url, params={"token": token, "section": "2"})).status_code, 401)

    async def test_admin_validation_csrf_and_self_protection(self):
        csrf = await self.admin_login()
        for path in ("/admin/users/new", "/admin/users/2/edit", "/admin/users/2/delete"):
            self.assertEqual((await self.client.post(path, data={})).status_code, 403)
        for overrides in (
            {"section": ""},
            {"section": "1,2"},
            {"section": "999"},
            {"username": "alice"},
            {"password": "short"},
        ):
            data = {"username": "charlie", "password": self.password, "section": "1", "csrf": csrf} | overrides
            response = await self.client.post("/admin/users/new", data=data)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.db.query(User).count(), 2)
        response = await self.client.post(
            "/admin/users/1/edit", data={"username": "alice", "section": "1", "csrf": csrf}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.client.post("/admin/users/1/delete", data={"csrf": csrf})).status_code, 400)
        self.assertTrue(self.db.get(User, 1).is_admin)
        self.assertEqual((await self.client.get("/admin/users/999/edit")).status_code, 404)

    async def test_section_assignment_is_enforced_even_for_admin_tokens(self):
        await self.admin_login()
        for user in self.users:
            with patch("src.app.router.ScheduleSelector") as selector:
                for section in ("1", "1,2", "999"):
                    response = await self.client.get(
                        self.url, params={"token": user.calendar_token, "section": section}
                    )
                    self.assertEqual(response.status_code, 403)
                selector.assert_not_called()
        page = await self.client.get("/")
        self.assertIn("section=2&", page.text)
        self.assertNotIn("section=1&", page.text)
        self.users[0].section = None
        self.db.commit()
        page = await self.client.get("/")
        self.assertIn("Czekamy na Twoją sekcję", page.text)
        self.assertNotIn("webcal://", page.text)
        self.assertEqual(
            (
                await self.client.get(self.url, params={"token": self.users[0].calendar_token, "section": "2"})
            ).status_code,
            403,
        )

    async def test_password_reset_invalidates_session_and_section_change_revokes_old_link(self):
        csrf = await self.admin_login()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client:
            await self.login("bob", client=client)
            self.assertEqual((await client.get("/")).status_code, 200)
            response = await self.client.post(
                "/admin/users/2/edit",
                data={
                    "username": "bob",
                    "section": "1",
                    "password": "changed-password-123",
                    "csrf": csrf,
                },
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual((await client.get("/")).status_code, 303)
            self.assertTrue(verify_password("changed-password-123", self.db.get(User, 2).password_hash))
            self.assertEqual(
                (
                    await client.get(self.url, params={"token": self.users[1].calendar_token, "section": "2"})
                ).status_code,
                403,
            )

    async def test_user_controls_only_own_email_and_notification_preference(self):
        self.assertFalse(self.users[0].email_notifications)
        self.assertEqual((await self.client.get("/account")).status_code, 303)
        self.assertEqual((await self.client.post("/account", data={})).status_code, 303)
        await self.login()
        page = await self.client.get("/account")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["cache-control"], "no-store")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        self.assertEqual((await self.client.post("/account", data={"email": "alice@example.com"})).status_code, 403)
        for email in ("", "invalid-email"):
            response = await self.client.post(
                "/account", data={"email": email, "email_notifications": "true", "csrf": csrf}
            )
            self.assertEqual(response.status_code, 400)
            self.assertFalse(self.db.get(User, 1).email_notifications)
        response = await self.client.post(
            "/account",
            data={
                "email": " alice@EXAMPLE.COM ",
                "email_notifications": "true",
                "csrf": csrf,
                "user_id": "2",
                "is_admin": "true",
                "section": "1",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.db.get(User, 1).email, "alice@example.com")
        self.assertTrue(self.db.get(User, 1).email_notifications)
        self.assertFalse(self.db.get(User, 1).is_admin)
        self.assertEqual(self.db.get(User, 1).section, "2")
        self.assertIsNone(self.db.get(User, 2).email)
        self.assertIn("Ustawienia zostały zapisane", (await self.client.get("/account")).text)
        response = await self.client.post("/account", data={"email": "alice@example.com", "csrf": csrf})
        self.assertEqual(response.status_code, 303)
        self.assertFalse(self.db.get(User, 1).email_notifications)
        self.assertEqual(self.db.get(User, 1).email, "alice@example.com")

    async def test_admin_email_preferences_are_validated_and_persisted(self):
        csrf = await self.admin_login()
        data = {
            "username": "charlie",
            "password": self.password,
            "section": "1",
            "csrf": csrf,
            "email": "bad",
            "email_notifications": "true",
        }
        self.assertEqual((await self.client.post("/admin/users/new", data=data)).status_code, 400)
        data["email"] = "charlie@example.com"
        self.assertEqual((await self.client.post("/admin/users/new", data=data)).status_code, 303)
        created = self.db.query(User).filter_by(username="charlie").one()
        self.assertEqual(created.email, "charlie@example.com")
        self.assertTrue(created.email_notifications)
        page = await self.client.get(f"/admin/users/{created.id}/edit")
        self.assertIn('value="charlie@example.com"', page.text)
        self.assertIn('name="email_notifications" value="true" checked', page.text)
        data.update(email="changed@example.com", password="")
        data.pop("email_notifications")
        self.assertEqual((await self.client.post(f"/admin/users/{created.id}/edit", data=data)).status_code, 303)
        self.db.refresh(created)
        self.assertEqual(created.email, "changed@example.com")
        self.assertFalse(created.email_notifications)

    async def test_notifications_go_only_to_opted_in_addresses_with_complete_attachments(self):
        from src.config import settings

        self.users[0].email, self.users[0].email_notifications = "alice@example.com", True
        self.users[1].email, self.users[1].email_notifications = "bob@example.com", False
        self.db.add_all(
            [
                User(
                    username="charlie",
                    password_hash=self.password_hash,
                    calendar_token=secrets.token_urlsafe(32),
                    email="charlie@example.com",
                    email_notifications=True,
                ),
                User(
                    username="duplicate",
                    password_hash=self.password_hash,
                    calendar_token=secrets.token_urlsafe(32),
                    email="alice@example.com",
                    email_notifications=True,
                ),
                User(
                    username="no-email",
                    password_hash=self.password_hash,
                    calendar_token=secrets.token_urlsafe(32),
                    email_notifications=True,
                ),
            ]
        )
        self.db.commit()
        sent = []

        async def fake_send(**kwargs):
            attachment = kwargs["attachments"][0]
            self.assertFalse(kwargs.get("recipients"))
            sent.append((kwargs["bcc"], await attachment.read()))
            await attachment.close()  # FastAPI-Mail closes each attachment after reading it.
            return True

        with (
            patch.object(settings, "IS_EMAILS_SEND", True),
            patch("src.app.router.ScheduleService.delete_and_create_schedules", new=AsyncMock(return_value=(
                {"2": [dict(date="2026-10-21", day="Środa", group="A", mode="online", hours="08:00-09:00",
                            kind="Dodano", before=None, after={"name": "Anatomia"})]},
                ("plan.xlsx", b"schedule content"),
            ))),
            patch("src.app.schedule_changes.SMTPService") as smtp,
        ):
            smtp.return_value.send_mail = AsyncMock(side_effect=fake_send)
            response = await self.client.post(
                "/api/schedules", json=[]
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sorted(sent[0][0]), ["alice@example.com"])
        self.assertEqual(sorted(sent[1][0]), ["alice@example.com", "charlie@example.com"])
        self.assertEqual(sent[0][1], b"schedule content")

    async def test_self_password_change_validates_and_revokes_other_sessions(self):
        self.assertEqual((await self.client.get("/account/password")).status_code, 303)
        self.assertEqual((await self.client.post("/account/password")).status_code, 303)
        await self.login()
        page = await self.client.get("/account/password")
        self.assertEqual(page.headers["cache-control"], "no-store")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        data = {
            "csrf": csrf,
            "current_password": self.password,
            "password": "new-secure-password",
            "password_confirmation": "new-secure-password",
            "user_id": "2",
        }
        self.assertEqual((await self.client.post("/account/password", data=data | {"csrf": ""})).status_code, 403)
        for overrides in (
            {"current_password": "wrong"},
            {"password_confirmation": "different"},
            {"password": "short", "password_confirmation": "short"},
        ):
            response = await self.client.post("/account/password", data=data | overrides)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('value="new-secure-password"', response.text)
            self.assertTrue(verify_password(self.password, self.users[0].password_hash))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as other:
            await self.login(client=other)
            token = self.users[0].calendar_token
            response = await self.client.post("/account/password", data=data)
            self.assertEqual(response.status_code, 303)
            self.assertIn("Hasło zostało zmienione", (await self.client.get("/account")).text)
            self.assertEqual((await other.get("/")).status_code, 303)
            self.assertEqual(self.users[0].calendar_token, token)
            self.assertTrue(verify_password(self.password, self.users[1].password_hash))
            self.assertEqual((await self.login(password=self.password, client=other)).status_code, 401)
            self.assertEqual((await self.login(password=data["password"], client=other)).status_code, 303)

    async def test_admin_mail_authorization_validation_and_bcc(self):
        from src.config import settings
        from fastapi_mail import MessageType

        for method in (self.client.get, self.client.post):
            self.assertEqual((await method("/admin/mail")).status_code, 303)
        await self.login()
        for method in (self.client.get, self.client.post):
            self.assertEqual((await method("/admin/mail")).status_code, 403)
        self.client.cookies.clear()
        csrf = await self.admin_login()
        for user in self.users:
            user.email = "shared@example.com"
        self.db.commit()
        data = {
            "csrf": csrf, "subject": "Ważna wiadomość", "body": "<b>Zwykły tekst</b>",
            "recipients": ["shared@example.com", "shared@example.com"],
        }
        with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.admin.SMTPService") as smtp:
            smtp.return_value.send_mail = AsyncMock(return_value=True)
            page = await self.client.get("/admin/mail")
            self.assertEqual(page.text.count('value="shared@example.com"'), 1)
            self.assertEqual((await self.client.post("/admin/mail", data=data | {"csrf": ""})).status_code, 403)
            for overrides in (
                {"recipients": []}, {"recipients": ["outside@example.com"]},
                {"subject": " "}, {"body": " "}, {"subject": "Title\r\nBcc: outside@example.com"},
            ):
                self.assertEqual((await self.client.post("/admin/mail", data=data | overrides)).status_code, 400)
            smtp.assert_not_called()
            response = await self.client.post("/admin/mail", data=data)
            self.assertEqual(response.status_code, 303)
            smtp.return_value.send_mail.assert_awaited_once_with(
                recipients=[], bcc=["shared@example.com"], subject=data["subject"],
                body=data["body"], subtype=MessageType.plain,
            )
            self.assertIn("przekazana do serwera", (await self.client.get("/admin/mail")).text)
            self.assertNotIn("przekazana do serwera", (await self.client.get("/admin/mail")).text)

    async def test_admin_mail_disabled_and_smtp_failure(self):
        from src.config import settings

        csrf = await self.admin_login()
        self.users[0].email = "alice@example.com"
        self.db.commit()
        data = {"csrf": csrf, "subject": "Temat", "body": "Treść", "recipients": ["alice@example.com"]}
        with patch("src.app.admin.SMTPService") as smtp:
            with patch.object(settings, "IS_EMAILS_SEND", False):
                self.assertEqual((await self.client.post("/admin/mail", data=data)).status_code, 400)
                smtp.assert_not_called()
            with patch.object(settings, "IS_EMAILS_SEND", True):
                smtp.return_value.send_mail = AsyncMock(side_effect=OSError("private SMTP error"))
                response = await self.client.post("/admin/mail", data=data)
                self.assertEqual(response.status_code, 502)
                self.assertIn('value="Temat"', response.text)
                self.assertIn('value="alice@example.com" checked', response.text)
                self.assertNotIn("private SMTP error", response.text)

    async def test_bcc_addresses_are_in_envelope_only(self):
        from aiosmtplib import SMTP
        from fastapi_mail import MessageType
        from src.app.config import smtp_config
        from src.app.services import SMTPService

        transport = SMTP()
        transport._ehlo_or_helo_if_needed = AsyncMock()
        transport.sendmail = AsyncMock(return_value=({}, "OK"))
        connection = AsyncMock()
        connection.__aenter__.return_value = SimpleNamespace(session=transport)
        with patch("fastapi_mail.fastmail.Connection", return_value=connection):
            await SMTPService(smtp_config).send_mail(
                recipients=[], bcc=["alice@example.com", "bob@example.com"],
                subject="Test", body="Wiadomość", subtype=MessageType.plain,
            )
        sender, envelope, wire = transport.sendmail.await_args.args
        self.assertEqual(set(envelope), {smtp_config.MAIL_FROM, "alice@example.com", "bob@example.com"})
        self.assertNotIn(b"Bcc:", wire)
        self.assertNotIn(b"alice@example.com", wire)
        self.assertNotIn(b"bob@example.com", wire)

    async def request_recovery(self, username="alice"):
        from src.config import settings

        page = await self.client.get("/forgot-password")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        with (
            patch.object(settings, "IS_EMAILS_SEND", True),
            patch.object(settings, "PUBLIC_BASE_URL", "https://studify.example"),
            patch("src.app.password_reset.SMTPService") as smtp,
        ):
            smtp.return_value.send_mail = AsyncMock(return_value=True)
            response = await self.client.post(
                "/forgot-password", data={"csrf": csrf, "username": username},
                headers={"host": "attacker.example"},
            )
            call = smtp.return_value.send_mail.await_args
        return response, csrf, call

    async def test_recovery_roundtrip_single_use_and_session_revocation(self):
        self.users[0].email = "alice@example.com"
        self.db.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as other:
            await self.login(client=other)
            response, csrf, call = await self.request_recovery()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(call.kwargs["recipients"], ["alice@example.com"])
            self.assertNotIn("attacker.example", call.kwargs["body"])
            token = re.search(r"https://studify.example/reset-password\?token=(\S+)", call.kwargs["body"]).group(1)
            self.assertNotIn(token, self.users[0].password_reset_hash)
            self.assertNotIn(token, response.text)
            for _ in range(2):
                page = await self.client.get("/reset-password", params={"token": token})
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.headers["referrer-policy"], "no-referrer")
                self.assertEqual(page.headers["cache-control"], "no-store")
            data = {"csrf": csrf, "token": token, "password": "recovered-password-123",
                    "password_confirmation": "recovered-password-123"}
            self.assertEqual((await self.client.post("/reset-password", data=data | {"csrf": ""})).status_code, 403)
            for changes in ({"password_confirmation": "different"},
                            {"password": "short", "password_confirmation": "short"}):
                self.assertEqual((await self.client.post("/reset-password", data=data | changes)).status_code, 400)
            self.assertEqual((await self.client.post("/reset-password", data=data)).status_code, 303)
            self.assertIsNone(self.users[0].password_reset_hash)
            self.assertTrue(verify_password(data["password"], self.users[0].password_hash))
            self.assertTrue(verify_password(self.password, self.users[1].password_hash))
            self.assertEqual((await other.get("/")).status_code, 303)
            login = await self.client.get("/login")
            self.assertIn("Hasło zostało zmienione", login.text)
            csrf = re.search(r'name="csrf" value="([^"]+)"', login.text).group(1)
            self.assertEqual((await self.client.post("/reset-password", data=data | {"csrf": csrf})).status_code, 400)
            self.assertEqual((await self.login(password=data["password"])).status_code, 303)

    async def test_recovery_unknown_account_no_email_and_cooldown(self):
        unknown, _, call = await self.request_recovery("missing")
        self.assertIsNone(call)
        without_email, _, call = await self.request_recovery()
        self.assertIsNone(call)
        self.users[0].email = "alice@example.com"
        self.db.commit()
        known, _, call = await self.request_recovery()
        self.assertIsNotNone(call)
        digest = self.users[0].password_reset_hash
        repeated, _, call = await self.request_recovery()
        self.assertIsNone(call)
        self.assertEqual(digest, self.users[0].password_reset_hash)
        self.assertEqual(unknown.text, without_email.text)
        self.assertEqual(unknown.text, known.text)
        self.assertEqual(unknown.text, repeated.text)

    async def test_recovery_invalid_expired_replaced_and_changed_account_tokens(self):
        self.users[0].email = "alice@example.com"
        self.db.commit()
        _, _, call = await self.request_recovery()
        token = re.search(r"token=(\S+)", call.kwargs["body"]).group(1)
        for bad in ("", "bad", "9999999999." + "x" * 43, "2." + token.split(".")[1], token[:-1] + "!"):
            self.assertEqual((await self.client.get("/reset-password", params={"token": bad})).status_code, 400)
        original = self.users[0].password_reset_expires
        self.users[0].password_reset_expires = 1
        self.db.commit()
        self.assertEqual((await self.client.get("/reset-password", params={"token": token})).status_code, 400)
        self.users[0].password_reset_expires = original
        for field, value in (("email", "changed@example.com"), ("password_hash", "changed"),
                             ("session_version", 5)):
            previous = getattr(self.users[0], field)
            setattr(self.users[0], field, value)
            self.db.commit()
            self.assertEqual((await self.client.get("/reset-password", params={"token": token})).status_code, 400)
            setattr(self.users[0], field, previous)
            self.db.commit()
        self.users[0].password_reset_sent_at = 1
        self.db.commit()
        _, _, new_call = await self.request_recovery()
        self.assertIsNotNone(new_call)
        self.assertEqual((await self.client.get("/reset-password", params={"token": token})).status_code, 400)

    async def test_recovery_configuration_csrf_and_smtp_errors(self):
        from src.config import settings
        from src.app.password_reset import send_recovery_mail

        page = await self.client.get("/forgot-password")
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        data = {"username": "alice", "csrf": csrf}
        self.assertEqual((await self.client.post("/forgot-password", data=data | {"csrf": ""})).status_code, 403)
        for enabled, origin in ((False, "https://studify.example"), (True, None), (False, None)):
            with patch.object(settings, "IS_EMAILS_SEND", enabled), patch.object(settings, "PUBLIC_BASE_URL", origin):
                with self.assertLogs("src.app.password_reset", level="ERROR") as logs:
                    response = await self.client.post("/forgot-password", data=data)
                self.assertEqual(response.status_code, 503)
                self.assertEqual("Ustaw IS_EMAILS_SEND=1" in response.text, not enabled)
                self.assertEqual("Ustaw PUBLIC_BASE_URL" in response.text, origin is None)
                self.assertIn("Vault backend/app", response.text)
                self.assertIn("password_recovery.configuration_unavailable", "".join(logs.output))
                self.assertIn(f"IS_EMAILS_SEND={enabled}", "".join(logs.output))
                self.assertIn(f"PUBLIC_BASE_URL_configured={bool(origin)}", "".join(logs.output))
        with patch("src.app.password_reset.SMTPService") as smtp:
            smtp.return_value.send_mail = AsyncMock(side_effect=OSError("secret SMTP error"))
            with self.assertLogs("src.app.password_reset", level="WARNING") as logs:
                await send_recovery_mail("alice@example.com", "https://studify.example/reset-password?token=secret")
            self.assertNotIn("secret", "".join(logs.output))
            self.assertNotIn("alice@example.com", "".join(logs.output))
            self.assertIn("password_recovery.smtp_failed", "".join(logs.output))
            self.assertIn("cause=OSError", "".join(logs.output))

    async def test_recovery_smtp_diagnostics_unwrap_failures_without_leaking_secrets(self):
        import socket
        import ssl
        from aiosmtplib.errors import SMTPAuthenticationError, SMTPSenderRefused
        from fastapi_mail.errors import ConnectionErrors
        from src.app.password_reset import send_recovery_mail

        sensitive = "private-password alice@example.com token=private-token"
        cases = (
            (SMTPAuthenticationError(535, sensitive), "MAIL_PASSWORD", "smtp_code=535"),
            (SMTPSenderRefused(550, sensitive, "alice@example.com"), "MAIL_FROM", "smtp_code=550"),
            (socket.gaierror(-2, sensitive), "DNS", "errno=-2"),
            (ConnectionRefusedError(111, sensitive), "MAIL_PORT", "errno=111"),
            (TimeoutError(sensitive), "firewall", "cause=TimeoutError"),
            (ssl.SSLError(sensitive), "certyfikat", "cause=SSLError"),
        )
        for original, hint, detail in cases:
            async def fail(*args, **kwargs):
                try:
                    raise original
                except Exception:
                    # Match FastAPI-Mail's implicit exception context.
                    raise ConnectionErrors(sensitive)

            with patch("src.app.password_reset.SMTPService") as smtp:
                smtp.return_value.send_mail = AsyncMock(side_effect=fail)
                with self.assertLogs("src.app.password_reset", level="ERROR") as logs:
                    await send_recovery_mail("alice@example.com", "https://example.com/?token=private-token")
            output = "".join(logs.output)
            self.assertIn("exception=ConnectionErrors", output)
            self.assertIn(hint, output)
            self.assertIn(detail, output)
            self.assertIn("use_credentials=", output)
            self.assertIn("sender_matches_username=", output)
            for value in ("private-password", "alice@example.com", "private-token"):
                self.assertNotIn(value, output)

    async def test_no_mail_when_disabled_unchanged_or_no_subscribers(self):
        from src.config import settings

        for enabled, changed, subscribed in ((True, True, False), (False, True, True), (True, False, True)):
            self.users[0].email, self.users[0].email_notifications = "alice@example.com", subscribed
            self.db.commit()
            with (
                patch.object(settings, "IS_EMAILS_SEND", enabled),
                patch(
                    "src.app.router.ScheduleFileService.create_or_update_md5_file", new=AsyncMock(return_value=changed)
                ),
                patch("src.app.schedule_changes.SMTPService") as smtp,
            ):
                response = await self.client.post("/api/schedules/files", files={"file": ("plan.xlsx", b"schedule")})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["is_email_sent"])
                smtp.assert_not_called()


class PasswordTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verified(self):
        password = "my-strong-password"
        first, second = hash_password(password), hash_password(password)
        self.assertNotEqual(first, second)
        self.assertNotIn(password, first)
        self.assertTrue(verify_password(password, first))
        self.assertFalse(verify_password("wrong-password", first))
        self.assertFalse(verify_password("x" * 1025, first))
        self.assertFalse(verify_password(password, "invalid"))
        with self.assertRaises(ValueError):
            hash_password("short")
