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
            sent.append((kwargs["recipients"], await attachment.read()))
            await attachment.close()  # FastAPI-Mail closes each attachment after reading it.
            return True

        with (
            patch.object(settings, "IS_EMAILS_SEND", True),
            patch("src.app.router.ScheduleFileService.create_or_update_md5_file", new=AsyncMock(return_value=True)),
            patch("src.app.router.SMTPService") as smtp,
        ):
            smtp.return_value.send_mail = AsyncMock(side_effect=fake_send)
            response = await self.client.post(
                "/api/schedules/files", files={"file": ("plan.xlsx", b"schedule content")}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_email_sent"])
        self.assertEqual(sorted(addresses[0] for addresses, _ in sent), ["alice@example.com", "charlie@example.com"])
        self.assertTrue(all(len(addresses) == 1 and content == b"schedule content" for addresses, content in sent))

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
                patch("src.app.router.SMTPService") as smtp,
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
