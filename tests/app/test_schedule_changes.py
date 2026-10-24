import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date
from unittest.mock import AsyncMock, patch

# Reuse the test environment configured before importing application modules.
from test_calendar_auth import AsyncDatabaseAdapter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from tempfile import SpooledTemporaryFile

from src.app.models import PreviousSchedule, Schedule, ScheduleFile, User
from src.app.config import smtp_config
from src.app.schemas import ScheduleCreate
from src.app.services import ScheduleFileService, ScheduleService
from src.app.schedule_changes import compare_schedules, send_schedule_notifications
from src.config import settings


def lesson(section="1", name="Anatomia", **kwargs):
    return ScheduleCreate(
        date=date(2026, 10, 21),
        day_of_week="Środa",
        group="A",
        section=section,
        mode="Stacjonarnie",
        hours={"08:00-09:00": {"name": name, "uid": "random", **kwargs}},
    )


class DatabaseAdapter(AsyncDatabaseAdapter):
    @property
    def bind(self):
        return self.session.bind

    async def execute(self, statement, *args):
        return self.session.execute(statement, *args)

    @asynccontextmanager
    async def begin(self):
        with self.session.begin():
            yield


class ScheduleChangesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (Schedule, PreviousSchedule, ScheduleFile, User):
            model.__table__.create(self.engine)
        self.session = Session(self.engine)
        self.db = DatabaseAdapter(self.session)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_generated_uids_and_order_are_ignored(self):
        old = [lesson("1"), lesson("2")]
        new = deepcopy(old[::-1])
        for row in new:
            row.hours["08:00-09:00"]["uid"] = "new"
        self.assertEqual(compare_schedules(old, new), {})

    def test_calendar_keeps_address_without_geocoding_result(self):
        from icalendar import Calendar
        from src.app.services import ICalendarService

        schedule = lesson(location="Szpital, ul. Długa 12", lat=None, lng=None)
        prepared = ScheduleService._prepare_data([schedule])
        result = ICalendarService.serialize_calendar(prepared, "inperson", "1")
        event = Calendar.from_ical(result).walk("VEVENT")[0]
        self.assertEqual(str(event["LOCATION"]), "Szpital, ul. Długa 12")
        self.assertNotIn("X-APPLE-STRUCTURED-LOCATION", event)

    def test_online_calendar_prefers_hours_in_name_after_merging_grid_blocks(self):
        from icalendar import Calendar
        from zoneinfo import ZoneInfo
        from src.app.services import ICalendarService, ICSService

        cases = [
            ("Medycyna rodzinna (IX-X) dr n. med.  Żurek Marek wykład ONLINE\ngodz. 17:00-18:30", "17:00", "18:30"),
            ("Wykład online godz. 9:00 – 10:30", "09:00", "10:30"),
            ("Wykład Online 17:00 — 18:30", "17:00", "18:30"),
            ("Wykład ONLINE", "08:00", "10:00"),
            ("Wykład ONLINE godz. 25:00-26:30", "08:00", "10:00"),
            ("Wykład ONLINE godz. 18:30-17:00", "08:00", "10:00"),
            ("Wykład ONLINE godz. 17:00", "08:00", "10:00"),
            ("Wykład godz. 17:00-18:30", "08:00", "10:00"),
        ]
        for serializer in (ICalendarService, ICSService):
            for name, start, end in cases:
                with self.subTest(serializer=serializer.__name__, name=name):
                    schedule = lesson(name=name)
                    schedule.hours["09:05-10:00"] = dict(schedule.hours["08:00-09:00"])
                    prepared = ScheduleService._prepare_data([schedule])
                    original = deepcopy(prepared)
                    events_type = "online" if "online" in name.lower() else "inperson"
                    events = Calendar.from_ical(serializer.serialize_calendar(prepared, events_type, "1")).walk("VEVENT")
                    self.assertEqual(len(events), 1)
                    event = events[0]
                    for field, expected in (("DTSTART", start), ("DTEND", end)):
                        value = event.decoded(field).astimezone(ZoneInfo("Europe/Warsaw"))
                        self.assertEqual(value.strftime("%H:%M"), expected)
                        self.assertEqual(value.date(), schedule.date)
                    self.assertEqual(str(event["SUMMARY"]), name)
                    self.assertEqual(str(event["UID"]), "random")
                    self.assertEqual(prepared, original)

    def test_calendar_descriptions_link_to_google_maps(self):
        from urllib.parse import parse_qs, urlsplit
        from icalendar import Calendar
        from src.app.services import ICalendarService, ICSService

        cases = [
            ({"location": "Szpital, ul. Długa 12", "lat": 50.1, "lng": 19.2}, "50.1,19.2"),
            ({"location": "Szpital Łódź & Klinika, ul. Długa 12", "lat": None, "lng": None},
             "Szpital Łódź & Klinika, ul. Długa 12"),
            ({"location": "Szpital", "lat": 0, "lng": 0}, "0,0"),
            ({}, None),
        ]
        for serializer in (ICalendarService, ICSService):
            for location, query in cases:
                with self.subTest(serializer=serializer.__name__, location=location):
                    prepared = ScheduleService._prepare_data([lesson(**location)])
                    event = Calendar.from_ical(serializer.serialize_calendar(prepared, "inperson", "1")).walk("VEVENT")[0]
                    if query is None:
                        self.assertNotIn("DESCRIPTION", event)
                        continue
                    url = str(event["DESCRIPTION"]).removeprefix("Google Maps: ")
                    parsed = urlsplit(url)
                    self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                                     ("https", "www.google.com", "/maps/search/"))
                    self.assertEqual(parse_qs(parsed.query), {"api": ["1"], "query": [query]})
                    if serializer is ICalendarService and location.get("lat") is not None:
                        self.assertEqual(str(event["X-APPLE-STRUCTURED-LOCATION"]), f"geo:{query}")

    def test_adjacent_and_five_minute_lessons_produce_one_change(self):
        for second_start in ("09:00", "09:05"):
            old, new = lesson(), lesson(name="Histologia")
            for schedule in (old, new):
                schedule.hours[f"{second_start}-10:00"] = dict(schedule.hours["08:00-09:00"], uid="second")
            changes = compare_schedules([old], [new])["1"]
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0]["hours"], "08:00-10:00")
            self.assertEqual(changes[0]["kind"], "Zmieniono")
            self.assertEqual(changes[0]["before"]["name"], "Anatomia")
            self.assertEqual(changes[0]["after"]["name"], "Histologia")
            for before, after, kind in (([], [new], "Dodano"), ([old], [], "Usunięto")):
                rows = compare_schedules(before, after)["1"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["hours"], "08:00-10:00")
                self.assertEqual(rows[0]["kind"], kind)

    def test_equivalent_block_splitting_does_not_send_notification(self):
        old = lesson()
        old.hours["09:05-10:00"] = {"name": "Anatomia", "uid": "another"}
        new = lesson()
        new.hours = {"08:00-10:00": {"name": "Anatomia", "uid": "new"}}
        self.assertEqual(compare_schedules([old], [new]), {})

    def test_longer_breaks_different_names_and_separate_days_are_not_merged(self):
        for start, name in (("09:10", "Anatomia"), ("09:05", "Histologia")):
            schedule = lesson()
            schedule.hours[f"{start}-10:00"] = {"name": name}
            self.assertEqual(len(compare_schedules([], [schedule])["1"]), 2)
        self.assertEqual(
            len(compare_schedules([], [lesson(), lesson().model_copy(update={"date": date(2026, 10, 22)})])["1"]), 2
        )

    def test_email_addresses_link_to_google_maps_before_and_after_changes(self):
        import re
        from html import unescape
        from urllib.parse import parse_qs, urlsplit
        from starlette.templating import Jinja2Templates

        address = 'Szpital Łódź & Klinika <A>, ul. Długa 12'
        for coordinates, query in (({}, address), ({"lat": 50.1, "lng": 19.2}, "50.1,19.2"),
                                   ({"lat": 0, "lng": 0}, "0,0")):
            with self.subTest(coordinates=coordinates):
                old = lesson(location=address, **coordinates)
                new = lesson(name="Histologia", location=address, **coordinates)
                rows = compare_schedules([old], [new])["1"]
                html = Jinja2Templates(directory="templates").get_template("mail.html").render(
                    section="1", changes=rows, has_attachment=False
                )
                links = re.findall(r'href="(https://www.google.com/maps/search/[^\"]+)"', html)
                self.assertEqual(len(links), 2)
                for link in links:
                    self.assertEqual(parse_qs(urlsplit(unescape(link)).query), {"api": ["1"], "query": [query]})
                self.assertIn('Szpital Łódź &amp; Klinika &lt;A&gt;', html)
                self.assertNotIn('Klinika <A>', html)

    def test_email_renders_merged_block_and_all_change_colors(self):
        from starlette.templating import Jinja2Templates

        old, new = lesson(), lesson(name="Histologia")
        for schedule in (old, new):
            schedule.hours["09:05-10:00"] = dict(schedule.hours["08:00-09:00"])
        rows = compare_schedules([old], [new])["1"]
        rows += compare_schedules([], [lesson(name="Dodane")])["1"]
        rows += compare_schedules([lesson(name="Usunięte")], [])["1"]
        html = (
            Jinja2Templates(directory="templates")
            .get_template("mail.html")
            .render(section="1", changes=rows, has_attachment=False)
        )
        self.assertEqual(html.count("08:00-10:00"), 1)
        self.assertNotIn("09:05-10:00", html)
        for color in ("#f0fdf4", "#fef2f2", "#fffbeb"):
            self.assertIn(color, html)
        self.assertIn("Liczba zmian: 3", html)
        self.assertNotIn("Pełny plan zajęć znajdziesz w załączniku", html)

    def test_changes_include_added_removed_changed_and_only_affected_sections(self):
        old = [lesson("1"), lesson("2"), lesson("3"), lesson("5")]
        new = [lesson("1", "Histologia"), lesson("2"), lesson("4"), lesson("5", location="Sala 4")]
        changes = compare_schedules(old, new)
        self.assertEqual(set(changes), {"1", "3", "4", "5"})
        self.assertEqual(changes["1"][0]["kind"], "Zmieniono")
        self.assertEqual(changes["3"][0]["kind"], "Usunięto")
        self.assertEqual(changes["4"][0]["kind"], "Dodano")
        self.assertEqual(changes["5"][0]["after"]["location"], "Sala 4")

    def test_time_date_group_mode_changes_and_duplicates(self):
        for field, value in (("date", date(2026, 10, 22)), ("group", "B"), ("mode", "Online")):
            old = lesson()
            new = old.model_copy(update={field: value})
            self.assertEqual(len(compare_schedules([old], [new])["1"]), 2)
        new = lesson()
        new.hours = {"09:00-10:00": new.hours["08:00-09:00"]}
        rows = compare_schedules([lesson()], [new])["1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "Zmieniono")
        self.assertEqual(rows[0]["before_hours"], "08:00-09:00")
        self.assertEqual(rows[0]["after_hours"], "09:00-10:00")
        self.assertEqual(compare_schedules([lesson(), lesson()], [lesson()])["1"][0]["kind"], "Usunięto")

    def test_shifted_merged_block_is_one_change_with_old_and_new_hours_in_email(self):
        from starlette.templating import Jinja2Templates

        old, new = lesson(), lesson()
        old.hours["09:05-10:00"] = {"name": "Anatomia"}
        new.hours = {"10:00-11:00": {"name": "Anatomia"}, "11:05-12:00": {"name": "Anatomia"}}
        rows = compare_schedules([old], [new])["1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "Zmieniono")
        self.assertEqual(rows[0]["before_hours"], "08:00-10:00")
        self.assertEqual(rows[0]["after_hours"], "10:00-12:00")
        body = (
            Jinja2Templates(directory="templates")
            .get_template("mail.html")
            .render(section="1", changes=rows, has_attachment=False)
        )
        self.assertIn("Godziny: 08:00-10:00", body)
        self.assertIn("Godziny: 10:00-12:00", body)

    def test_subjects_swapping_hours_are_matched_by_name(self):
        old, new = lesson(), lesson(name="Histologia")
        old.hours["10:00-11:00"] = {"name": "Histologia"}
        new.hours["10:00-11:00"] = {"name": "Anatomia"}
        rows = compare_schedules([old], [new])["1"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["kind"], "Zmieniono")
            self.assertEqual(row["before"]["name"], row["after"]["name"])
            self.assertNotEqual(row["before_hours"], row["after_hours"])

    def test_unchanged_repeated_lesson_is_preserved_and_other_occurrence_moves(self):
        old, new = lesson(), lesson()
        old.hours["12:00-13:00"] = {"name": "Anatomia"}
        new.hours["14:00-15:00"] = {"name": "Anatomia", "location": "Sala 2"}
        rows = compare_schedules([old], [new])["1"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["before_hours"], "12:00-13:00")
        self.assertEqual(rows[0]["after_hours"], "14:00-15:00")
        self.assertEqual(rows[0]["after"]["location"], "Sala 2")

    def test_different_subject_at_different_time_is_not_a_time_change(self):
        new = lesson(name="Histologia")
        new.hours = {"10:00-11:00": {"name": "Histologia"}}
        rows = compare_schedules([lesson()], [new])["1"]
        self.assertEqual({row["kind"] for row in rows}, {"Dodano", "Usunięto"})

    async def test_rotation_retains_exactly_current_and_previous_and_handles_empty_import(self):
        for name in ("A", "B", "C"):
            await ScheduleService.delete_and_create_schedules([lesson(name=name)], self.db)
        current = self.session.scalars(select(Schedule)).one()
        previous = self.session.scalars(select(PreviousSchedule)).one()
        self.assertEqual(current.hours["08:00-09:00"]["name"], "C")
        self.assertEqual(previous.hours["08:00-09:00"]["name"], "B")
        self.session.commit()
        changes, _ = await ScheduleService.delete_and_create_schedules([], self.db)
        self.assertEqual(changes["1"][0]["kind"], "Usunięto")
        self.assertEqual(self.session.scalars(select(Schedule)).all(), [])
        self.assertEqual(len(self.session.scalars(select(PreviousSchedule)).all()), 1)

    async def test_failed_import_rolls_back_both_versions(self):
        await ScheduleService.delete_and_create_schedules([lesson(name="A")], self.db)
        await ScheduleService.delete_and_create_schedules([lesson(name="B")], self.db)
        execute = self.db.execute

        async def fail_insert(statement, *args):
            if getattr(statement, "is_insert", False) and statement.table.name == "schedules":
                raise RuntimeError("import failed")
            return await execute(statement, *args)

        with patch.object(self.db, "execute", side_effect=fail_insert):
            with self.assertRaises(RuntimeError):
                await ScheduleService.delete_and_create_schedules([lesson(name="C")], self.db)
        self.assertEqual(self.session.scalars(select(Schedule)).one().hours["08:00-09:00"]["name"], "B")
        self.assertEqual(self.session.scalars(select(PreviousSchedule)).one().hours["08:00-09:00"]["name"], "A")

    async def test_upload_is_staged_once_and_duplicate_hash_does_not_trigger_mail(self):
        for attempt in range(2):
            with SpooledTemporaryFile() as source:
                source.write(b"excel")
                source.seek(0)
                await ScheduleFileService.create_or_update_md5_file(
                    file=UploadFile(file=source, filename="plan.xls"), db=self.db
                )
            self.session.commit()
            _, attachment = await ScheduleService.delete_and_create_schedules([lesson()], self.db)
            self.assertEqual(attachment, ("plan.xls", b"excel") if attempt == 0 else None)
        _, attachment = await ScheduleService.delete_and_create_schedules([lesson()], self.db)
        self.assertIsNone(attachment)

    async def test_per_section_mail_filters_recipients_escapes_html_and_copies_attachment(self):
        for index, (section, email, subscribed) in enumerate(
            [
                ("1", "one@example.com", True),
                ("1", "one@example.com", True),
                ("2", "two@example.com", True),
                ("3", "unchanged@example.com", True),
                ("1", "disabled@example.com", False),
                (None, "unassigned@example.com", True),
            ]
        ):
            self.session.add(
                User(
                    username=str(index),
                    password_hash="unused",
                    calendar_token=str(index),
                    section=section,
                    email=email,
                    email_notifications=subscribed,
                )
            )
        self.session.commit()
        changes = compare_schedules([lesson("3")], [lesson("1", "<script>"), lesson("2", "Histologia"), lesson("3")])
        sent = []

        async def send(**kwargs):
            upload = kwargs["attachments"][0]
            sent.append((kwargs, await upload.read()))
            await upload.close()
            return True

        with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.schedule_changes.SMTPService") as smtp:
            smtp.return_value.send_mail = AsyncMock(side_effect=send)
            self.assertTrue(
                await send_schedule_notifications(changes=changes, attachment=("plan.xls", b"excel"), db=self.db)
            )
        self.assertEqual(
            [mail[0]["bcc"] for mail in sent],
            [["one@example.com", smtp_config.MAIL_FROM], ["two@example.com", smtp_config.MAIL_FROM],
             ["unchanged@example.com"], ["unassigned@example.com"]],
        )
        self.assertIn("Brak zmian w Twojej sekcji", sent[2][0]["body"])
        self.assertIn("Liczba zmian: 0", sent[2][0]["body"])
        self.assertIn("Nie masz przypisanej sekcji", sent[3][0]["body"])
        self.assertTrue(all(content == b"excel" for _, content in sent))
        self.assertIn("&lt;script&gt;", sent[0][0]["body"])
        self.assertNotIn("Histologia", sent[0][0]["body"])
        self.assertIn("Tabela zmian", sent[0][0]["body"])

    async def test_changed_sections_without_subscribers_notify_sender(self):
        self.session.add(User(
            username="disabled", password_hash="unused", calendar_token="disabled",
            section="2", email="disabled@example.com", email_notifications=False,
        ))
        self.session.commit()
        changes = compare_schedules([], [lesson("1", "Anatomia"), lesson("2", "Histologia")])
        for attachment in (None, ("plan.xls", b"excel")):
            with self.subTest(attachment=attachment):
                sent = []

                async def send(**kwargs):
                    content = await kwargs["attachments"][0].read() if kwargs["attachments"] else None
                    sent.append((kwargs, content))
                    return True

                with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.schedule_changes.SMTPService") as smtp:
                    smtp.return_value.send_mail = AsyncMock(side_effect=send)
                    self.assertTrue(await send_schedule_notifications(changes=changes, attachment=attachment, db=self.db))
                self.assertEqual(len(sent), 2)
                for index, (mail, content) in enumerate(sent, 1):
                    self.assertEqual(mail["bcc"], [smtp_config.MAIL_FROM])
                    self.assertIn(f"Sekcja {index}", mail["subject"])
                    self.assertIn("Liczba zmian: 1", mail["body"])
                    self.assertEqual(content, b"excel" if attachment else None)

    async def test_empty_changes_without_subscribers_do_not_notify_sender(self):
        with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.schedule_changes.SMTPService") as smtp:
            self.assertFalse(await send_schedule_notifications(
                changes={"1": []}, attachment=("plan.xls", b"excel"), db=self.db,
            ))
            smtp.assert_not_called()

    async def test_upload_then_import_notifies_for_new_files_but_not_duplicate_uploads(self):
        from httpx import ASGITransport, AsyncClient
        from src.main import app
        from src.database import get_db

        await ScheduleService.delete_and_create_schedules([lesson()], self.db)
        self.session.add(
            User(
                username="one",
                password_hash="unused",
                calendar_token="one",
                section="1",
                email="one@example.com",
                email_notifications=True,
            )
        )
        self.session.commit()

        async def database():
            yield self.db
            self.session.commit()

        async def send(**kwargs):
            self.assertEqual(self.session.scalars(select(Schedule)).one().hours["08:00-09:00"]["name"], "Histologia")
            self.assertIn(await kwargs["attachments"][0].read(), (b"excel", b"new excel"))
            return True

        app.dependency_overrides[get_db] = database
        try:
            with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.schedule_changes.SMTPService") as smtp:
                smtp.return_value.send_mail = AsyncMock(side_effect=send)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver") as client:
                    for content in (b"excel", b"excel", b"new excel"):
                        upload = await client.post("/api/schedules/files", files={"file": ("plan.xls", content)})
                        self.assertEqual(upload.status_code, 200)
                        self.assertFalse(upload.json()["is_email_sent"])
                        response = await client.post(
                            "/api/schedules", json=[lesson(name="Histologia").model_dump(mode="json")]
                        )
                        self.assertEqual(response.status_code, 200)
                self.assertEqual(smtp.return_value.send_mail.await_count, 2)
                self.assertIn("Brak zmian w Twojej sekcji", smtp.return_value.send_mail.await_args.kwargs["body"])
        finally:
            app.dependency_overrides.clear()

    async def test_new_file_without_section_changes_notifies_subscribers(self):
        self.session.add(
            User(
                username="one",
                password_hash="unused",
                calendar_token="one",
                section="1",
                email="one@example.com",
                email_notifications=True,
            )
        )
        self.session.commit()
        with patch.object(settings, "IS_EMAILS_SEND", True), patch("src.app.schedule_changes.SMTPService") as smtp:
            smtp.return_value.send_mail = AsyncMock(return_value=True)
            self.assertTrue(
                await send_schedule_notifications(changes={}, attachment=("plan.xls", b"new file"), db=self.db)
            )
            body = smtp.return_value.send_mail.await_args.kwargs["body"]
            self.assertEqual(smtp.return_value.send_mail.await_args.kwargs["bcc"], ["one@example.com"])
            self.assertIn("Brak zmian w Twojej sekcji", body)
            self.assertIn("Liczba zmian: 0", body)
            self.assertNotIn("<tr", body.split("<tbody>")[1].split("</tbody>")[0])

    async def test_no_mail_without_changes_or_when_disabled(self):
        with patch("src.app.schedule_changes.SMTPService") as smtp:
            with patch.object(settings, "IS_EMAILS_SEND", True):
                self.assertFalse(await send_schedule_notifications(changes={}, attachment=None, db=self.db))
            with patch.object(settings, "IS_EMAILS_SEND", False):
                self.assertFalse(await send_schedule_notifications(changes={"1": []}, attachment=None, db=self.db))
            smtp.assert_not_called()
