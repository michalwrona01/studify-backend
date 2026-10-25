import re
import unittest
from contextlib import contextmanager
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from unittest.mock import AsyncMock, MagicMock, patch

import test_calendar_auth as auth_tests
from src.app.config import mailbox_config
from src.app.mailbox_service import (
    MailboxError,
    MailboxService,
    addresses,
    compose_message,
    incoming_html,
    message_data,
)
from src.config import settings


class MailboxServiceTests(unittest.TestCase):
    def test_incoming_html_blocks_scripts_images_and_tracking(self):
        result = incoming_html(
            '<script>alert(1)</script><img src="https://tracking.test/pixel">'
            '<p style="background:url(https://tracking.test)">Treść</p>'
            '<a href="javascript:alert(1)" onclick="alert(1)">Bad</a>'
            '<a href="https://example.com">Good</a><form action="https://example.com"><input></form>'
        )
        for value in ("<script", "<img", "tracking.test", "javascript:", "onclick", "<form", "<input"):
            self.assertNotIn(value, result)
        self.assertIn("Treść", result)
        self.assertIn("noopener noreferrer", result)

    def test_mime_parsing_handles_polish_headers_body_and_attachment(self):
        message = EmailMessage()
        message["Subject"] = "Zażółć gęślą jaźń"
        message["From"] = "Michał <michal@example.com>"
        message.set_content("Wiadomość")
        message.add_alternative('<p>Wiadomość HTML</p><img src="https://tracker.test">', subtype="html")
        message.add_attachment(b"file-data", maintype="application", subtype="pdf", filename="Plan zajęć.pdf")
        parsed = message_data(message.as_bytes())
        self.assertEqual(parsed["subject"], "Zażółć gęślą jaźń")
        self.assertEqual(parsed["text"].strip(), "Wiadomość")
        self.assertEqual(parsed["attachments"][0]["name"], "Plan zajęć.pdf")
        self.assertNotIn("tracker.test", parsed["html"])

    def test_compose_preserves_reply_thread_and_bcc_for_archive(self):
        mail, envelope = compose_message(
            "one@example.com",
            "",
            "hidden@example.com",
            "Test",
            "Treść",
            [("plan.txt", b"test")],
            {"message_id": "<original@example.com>", "references": "<root@example.com>"},
        )
        self.assertEqual(envelope, ["one@example.com", "hidden@example.com"])
        self.assertEqual(mail["Bcc"], "hidden@example.com")
        self.assertEqual(mail["In-Reply-To"], "<original@example.com>")
        self.assertEqual(str(mail["References"]), "<root@example.com> <original@example.com>")
        self.assertEqual(next(mail.iter_attachments()).get_payload(decode=True), b"test")

    def test_header_injection_and_invalid_addresses_rejected(self):
        for value in ("ok@example.com\r\nBcc: other@example.com", "invalid"):
            with self.assertRaises(MailboxError):
                addresses(value)
        with self.assertRaises(MailboxError):
            compose_message("one@example.com", "", "", "Title\nBcc: two@example.com", "Body")

    def test_incomplete_draft_can_be_saved_without_recipient(self):
        message, envelope = compose_message("", "", "", "", "Notatka", draft=True)
        self.assertEqual(envelope, [])
        self.assertIn("Notatka", message.get_content())

    def test_uidvalidity_is_checked_before_mutation(self):
        client = MagicMock()
        client.list_folders.return_value = [((), b"/", "INBOX")]
        client.select_folder.return_value = {b"UIDVALIDITY": 99}
        service = MailboxService()

        @contextmanager
        def connect():
            yield client

        with patch.object(service, "connect", connect), self.assertRaises(MailboxError):
            service.action("INBOX", 42, 98, "trash")
        client.move.assert_not_called()
        client.add_flags.assert_not_called()

    def test_trash_uses_uid_move_and_never_global_expunge(self):
        client = MagicMock()
        client.list_folders.return_value = [((), b"/", "INBOX"), ((b"\\Trash",), b"/", "Deleted Items")]
        client.select_folder.return_value = {b"UIDVALIDITY": 99}
        client.fetch.return_value = {42: {b"FLAGS": ()}}
        service = MailboxService()

        @contextmanager
        def connect():
            yield client

        with patch.object(service, "connect", connect):
            service.action("INBOX", 42, 99, "trash")
        client.move.assert_called_once_with([42], "Deleted Items")
        client.expunge.assert_not_called()

    def test_oversized_message_is_not_downloaded(self):
        client = MagicMock()
        client.list_folders.return_value = [((), b"/", "INBOX")]
        client.select_folder.return_value = {b"UIDVALIDITY": 99}
        client.fetch.return_value = {42: {b"RFC822.SIZE": 26 * 1024 * 1024}}
        with self.assertRaises(MailboxError):
            MailboxService().raw(client, "INBOX", 42, 99)
        client.fetch.assert_called_once_with([42], ["RFC822.SIZE"])

    def test_pagination_uses_uid_headers_and_keeps_messages_unread(self):
        client = MagicMock()
        client.list_folders.return_value = [((), b"/", "INBOX")]
        client.select_folder.return_value = {b"UIDVALIDITY": 99}
        client.search.return_value = list(range(1, 66))
        client.folder_status.return_value = {b"UNSEEN": 5}
        client.fetch.return_value = {
            35: {b"FLAGS": (), b"BODY[HEADER.FIELDS (SUBJECT FROM TO DATE)]": b"Subject: Hi\r\n\r\n"}
        }
        service = MailboxService()

        @contextmanager
        def connect():
            yield client

        with patch.object(service, "connect", connect):
            result = service.listing("INBOX", 2, "Zażółć")
        self.assertEqual(result["pages"], 3)
        self.assertEqual(result["messages"][0]["uid"], 35)
        self.assertTrue(result["messages"][0]["unread"])
        client.search.assert_called_once_with(["NOT", "DELETED", "TEXT", "Zażółć"], charset="UTF-8")
        client.fetch.assert_called_once_with(
            list(range(35, 5, -1)), ["FLAGS", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)]"]
        )


class MailboxRoutesTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(auth_tests.CalendarAuthorizationTests.setUpClass.__func__)
    asyncSetUp = auth_tests.CalendarAuthorizationTests.asyncSetUp
    asyncTearDown = auth_tests.CalendarAuthorizationTests.asyncTearDown
    login = auth_tests.CalendarAuthorizationTests.login

    async def make_admin(self):
        self.users[0].is_admin = True
        self.db.commit()
        await self.login()
        page = await self.client.get("/admin/poczta")
        return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)

    async def test_every_mailbox_endpoint_requires_admin(self):
        endpoints = [
            ("GET", ""),
            ("GET", "/message?folder=INBOX&uid=1&validity=1"),
            ("GET", "/attachment?folder=INBOX&uid=1&validity=1&part=1"),
            ("GET", "/compose"),
            ("POST", "/action"),
            ("POST", "/compose"),
        ]
        with patch("src.app.mailbox.MailboxService") as service:
            for method, path in endpoints:
                self.assertEqual((await self.client.request(method, "/admin/poczta" + path)).status_code, 303)
            await self.login()
            for method, path in endpoints:
                self.assertEqual((await self.client.request(method, "/admin/poczta" + path)).status_code, 403)
            service.assert_not_called()

    async def test_unconfigured_mailbox_is_safe_and_not_cached(self):
        with patch.object(mailbox_config, "IMAP_HOST", ""), patch("src.app.mailbox.MailboxService") as service:
            await self.make_admin()
            page = await self.client.get("/admin/poczta")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Połącz skrzynkę pocztową", page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        service.assert_not_called()

    async def test_actions_require_csrf_before_connecting(self):
        await self.make_admin()
        with patch("src.app.mailbox.MailboxService") as service:
            response = await self.client.post(
                "/admin/poczta/action", data={"folder": "INBOX", "uid": 1, "validity": 1, "action": "trash"}
            )
            self.assertEqual(response.status_code, 403)
            response = await self.client.post(
                "/admin/poczta/compose", data={"to": "one@example.com", "subject": "Hi", "body": "Hello"}
            )
            self.assertEqual(response.status_code, 403)
            service.assert_not_called()

    async def test_send_strips_bcc_and_saves_exact_message_with_attachment(self):
        csrf = await self.make_admin()
        saved = []
        sent = []

        async def run(operation, *args):
            if operation == service.append:
                saved.append(args)
            return "Sent"

        smtp = MagicMock()
        smtp.session.send_message = AsyncMock(
            side_effect=lambda message, **kwargs: sent.append((message, kwargs)) or ({}, "OK")
        )
        with (
            patch.object(mailbox_config, "IMAP_HOST", "imap.example.com"),
            patch.object(settings, "IS_EMAILS_SEND", True),
            patch("src.app.mailbox.MailboxService") as constructor,
            patch("src.app.mailbox.run_mail", side_effect=run),
            patch("src.app.mailbox.Connection") as connection,
        ):
            service = constructor.return_value
            connection.return_value.__aenter__ = AsyncMock(return_value=smtp)
            connection.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await self.client.post(
                "/admin/poczta/compose",
                data={
                    "csrf": csrf,
                    "to": "one@example.com",
                    "bcc": "hidden@example.com",
                    "subject": "Test",
                    "body": "Body",
                },
                files={"attachments": ("test.txt", b"attachment")},
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("Bcc", sent[0][0])
        self.assertIn("hidden@example.com", sent[0][1]["recipients"])
        archive = BytesParser(policy=policy.default).parsebytes(saved[0][1])
        self.assertEqual(str(archive["Bcc"]), "hidden@example.com")
        self.assertEqual(next(archive.iter_attachments()).get_payload(decode=True), b"attachment")

    async def test_sent_archive_failure_does_not_offer_to_resend(self):
        csrf = await self.make_admin()
        smtp = MagicMock()
        smtp.session.send_message = AsyncMock(return_value=({}, "OK"))
        with (
            patch.object(mailbox_config, "IMAP_HOST", "imap.example.com"),
            patch.object(settings, "IS_EMAILS_SEND", True),
            patch("src.app.mailbox.run_mail", side_effect=[None, MailboxError("failed")]),
            patch("src.app.mailbox.Connection") as connection,
        ):
            connection.return_value.__aenter__ = AsyncMock(return_value=smtp)
            connection.return_value.__aexit__ = AsyncMock(return_value=False)
            response = await self.client.post(
                "/admin/poczta/compose", data={"csrf": csrf, "to": "one@example.com", "subject": "Test", "body": "Body"}
            )
        self.assertEqual(response.status_code, 303)
        page = await self.client.get("/admin/poczta")
        self.assertIn("Nie wysyłaj wiadomości ponownie", page.text)
        smtp.session.send_message.assert_awaited_once()
