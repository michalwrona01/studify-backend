"""Compare lesson contents (not generated UIDs) and notify subscribers with section-specific changes."""

import json
from collections import Counter, defaultdict
from datetime import datetime
from tempfile import SpooledTemporaryFile
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from sqlalchemy import select
from starlette.datastructures import Headers
from starlette.templating import Jinja2Templates

from src.app.config import schedule_mail_config, smtp_config
from src.app.models import User
from src.app.services import SMTPService, ScheduleService
from src.config import settings


def _lessons(schedules):
    lessons = defaultdict(Counter)
    for schedule in schedules:
        # Use the same contiguous / five-minute-break blocks as the calendar.
        # Aggregate each version before comparing, so a changed block is one row.
        for hours, subject in ScheduleService._prepare_data([schedule])[0].items():
            # Dates are part of the key; generated UIDs are not lesson changes.
            content = {key: value for key, value in subject.items() if key not in {"uid", "day_begin", "day_end"}}
            key = (
                str(schedule.section),
                schedule.date.isoformat(),
                schedule.day_of_week,
                schedule.group,
                schedule.mode,
                hours,
            )
            lessons[key][json.dumps(content, sort_keys=True, ensure_ascii=False)] += 1
    return lessons


def _time_distance(before, after):
    def minutes(value):
        hour, minute = map(int, value.split(":"))
        return hour * 60 + minute

    return sum(abs(minutes(a) - minutes(b)) for a, b in zip(before.split("-"), after.split("-")))


def compare_schedules(previous, current):
    before, after = _lessons(previous), _lessons(current)
    removed, added = defaultdict(list), defaultdict(list)
    for key in sorted(before.keys() | after.keys()):
        # Cancel unchanged occurrences first, retaining duplicate lesson counts.
        removed[key[:-1]].extend((key[-1], value) for value in sorted((before[key] - after[key]).elements()))
        added[key[:-1]].extend((key[-1], value) for value in sorted((after[key] - before[key]).elements()))

    changes = defaultdict(list)
    for context in sorted(removed.keys() | added.keys()):
        old, new = removed[context], added[context]
        used_old, used_new = set(), set()
        pairs = []
        # Match the same lesson before comparing replacements at the same time.
        # This also handles two subjects swapping time slots. For repeated names,
        # prefer identical details, then the closest time, with stable tie breaks.
        candidates = sorted(
            (old_value != new_value, _time_distance(old_hours, new_hours), i, j)
            for i, (old_hours, old_value) in enumerate(old)
            for j, (new_hours, new_value) in enumerate(new)
            if json.loads(old_value).get("name")
            and json.loads(old_value).get("name") == json.loads(new_value).get("name")
        )
        for _, _, i, j in candidates:
            if i not in used_old and j not in used_new:
                pairs.append((old[i], new[j]))
                used_old.add(i)
                used_new.add(j)
        for i, entry in enumerate(old):
            if i in used_old:
                continue
            match = next((j for j, value in enumerate(new) if j not in used_new and value[0] == entry[0]), None)
            if match is not None:
                pairs.append((entry, new[match]))
                used_new.add(match)
            else:
                pairs.append((entry, None))
        pairs.extend((None, entry) for j, entry in enumerate(new) if j not in used_new)

        section, date, day, group, mode = context
        for old_entry, new_entry in sorted(pairs, key=lambda pair: (pair[1] or pair[0])[0]):
            old_hours, old_value = old_entry if old_entry else (None, None)
            new_hours, new_value = new_entry if new_entry else (None, None)
            changes[section].append(
                dict(
                    date=date,
                    day=day,
                    group=group,
                    mode=mode,
                    hours=new_hours or old_hours,
                    before_hours=old_hours,
                    after_hours=new_hours,
                    kind="Zmieniono" if old_entry and new_entry else "Dodano" if new_entry else "Usunięto",
                    before=json.loads(old_value) if old_value is not None else None,
                    after=json.loads(new_value) if new_value is not None else None,
                )
            )
    return dict(changes)


async def send_schedule_notifications(*, changes, attachment, db):
    if not settings.IS_EMAILS_SEND or (not changes and attachment is None):
        return False
    template = Jinja2Templates(directory="templates").get_template("mail.html")
    sent = False
    sections = (
        await db.scalars(
            select(User.section)
            .where(
                User.email_notifications.is_(True),
                User.email.is_not(None),
                User.email != "",
            )
            .distinct()
        )
    ).all()
    # Changes must reach the sender even when a section has no subscribers.
    sections = list(dict.fromkeys([*sections, *(section for section, rows in changes.items() if rows)]))
    for section in sections:
        rows = changes.get(section, [])
        recipients = (
            await db.scalars(
                select(User.email)
                .where(
                    User.section == section,
                    User.email_notifications.is_(True),
                    User.email.is_not(None),
                    User.email != "",
                )
                .distinct()
            )
        ).all()
        recipients = list(recipients)
        if rows and smtp_config.MAIL_FROM.casefold() not in {email.casefold() for email in recipients}:
            recipients.append(smtp_config.MAIL_FROM)
        if not recipients:
            continue
        upload = None
        try:
            if attachment is not None:
                filename, content = attachment
                upload = UploadFile(
                    file=SpooledTemporaryFile(max_size=1024 * 1024),
                    filename=filename,
                    headers=Headers({"content-type": "application/vnd.ms-excel"}),
                )
                await upload.write(content)
                await upload.seek(0)
            sent_at = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%d.%m.%Y %H:%M:%S %Z")
            subject = schedule_mail_config.MAIL_SUBJECT
            if section:
                subject += f" — Sekcja {section}"
            await SMTPService(config=smtp_config).send_mail(
                bcc=recipients,
                subject=f"{subject} — {sent_at}",
                body=template.render(section=section, changes=rows, has_attachment=upload is not None),
                attachments=[upload] if upload is not None else [],
            )
            sent = True
        finally:
            if upload is not None:
                await upload.close()
    return sent
