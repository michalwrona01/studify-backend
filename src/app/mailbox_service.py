"""IMAP mailbox operations. Each operation uses its own bounded TLS connection."""

import re
import ssl
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, make_msgid

from imapclient import IMAPClient
from pydantic import EmailStr, TypeAdapter

from src.app.config import mailbox_config, smtp_config
from src.app.mail_content import MailHTML

MAX_MESSAGE_BYTES = 25 * 1024 * 1024
PAGE_SIZE = 30
SPECIAL_FOLDERS = {
    b"\\Sent": ("sent", "Wysłane"),
    b"\\Drafts": ("drafts", "Szkice"),
    b"\\Junk": ("junk", "Spam"),
    b"\\Trash": ("trash", "Kosz"),
    b"\\Archive": ("archive", "Archiwum"),
}


class MailboxError(Exception):
    """Safe, user-facing error; never includes server replies or credentials."""


class IncomingHTML(MailHTML):
    # External images, embedded media and stylesheets are never fetched.
    tags = MailHTML.tags - {"img"}

    def handle_starttag(self, tag, attrs):
        attrs = [(key, value) for key, value in attrs if key not in {"id", "style", "target"}]
        if tag == "a":
            attrs.append(("target", "_blank"))
        super().handle_starttag(tag, attrs)


def incoming_html(body):
    parser = IncomingHTML()
    parser.feed(body)
    parser.close()
    content = "".join(parser.output)
    return (
        '<!doctype html><html lang="pl"><head><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        'style-src &#39;unsafe-inline&#39;; img-src &#39;none&#39;; form-action &#39;none&#39;">'
        "<style>body{font:15px/1.6 system-ui;color:#27272c;overflow-wrap:anywhere;padding:12px}"
        "table{max-width:100%}pre{white-space:pre-wrap}a{color:#245cb4}</style></head><body>"
        + content
        + "</body></html>"
    )


def part_text(part):
    raw = part.get_payload(decode=True) or b""
    try:
        return raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def message_data(raw):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    attachments = []
    for index, part in enumerate(message.walk()):
        if part.is_multipart():
            continue
        if part.get_filename() or part.get_content_disposition() == "attachment":
            attachments.append(
                {
                    "index": index,
                    "name": part.get_filename() or "załącznik",
                    "size": len(part.get_payload(decode=True) or b""),
                }
            )
    plain_text = part_text(plain) if plain else ""
    if not plain_text and html:
        parser = IncomingHTML()
        parser.feed(part_text(html))
        plain_text = "".join(parser.text)
    return {
        "subject": str(message.get("Subject", "(bez tematu)")),
        "sender": str(message.get("From", "")),
        "to": str(message.get("To", "")),
        "cc": str(message.get("Cc", "")),
        "bcc": str(message.get("Bcc", "")),
        "date": str(message.get("Date", "")),
        "reply_to": str(message.get("Reply-To") or message.get("From", "")),
        "message_id": str(message.get("Message-ID", "")),
        "references": str(message.get("References", "")),
        "text": plain_text,
        "html": incoming_html(part_text(html)) if html else "",
        "attachments": attachments,
    }


def addresses(value):
    if not value.strip():
        return []
    if "\r" in value or "\n" in value:
        raise MailboxError("Adresy odbiorców muszą być w jednym wierszu.")
    result = []
    for _, address in getaddresses([value.replace(";", ",")]):
        try:
            address = str(TypeAdapter(EmailStr).validate_python(address))
        except ValueError:
            raise MailboxError("Nieprawidłowy adres odbiorcy.") from None
        if address not in result:
            result.append(address)
    if len(result) > 50:
        raise MailboxError("Możesz wskazać maksymalnie 50 odbiorców.")
    return result


def compose_message(to, cc, bcc, subject, body, attachments=(), original=None, draft=False):
    recipients, copies, hidden = addresses(to), addresses(cc), addresses(bcc)
    if not draft and not recipients and not copies and not hidden:
        raise MailboxError("Podaj adres odbiorcy.")
    if not draft and (not subject.strip() or not body.strip()):
        raise MailboxError("Podaj temat i treść wiadomości.")
    if any(char in subject for char in "\r\n"):
        raise MailboxError("Temat musi mieścić się w jednym wierszu.")
    envelope = list(dict.fromkeys(recipients + copies + hidden))
    if len(envelope) > 50:
        raise MailboxError("Możesz wskazać maksymalnie 50 odbiorców.")
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = str(smtp_config.MAIL_FROM)
    if recipients:
        message["To"] = ", ".join(recipients)
    if copies:
        message["Cc"] = ", ".join(copies)
    # Bcc is only retained in the IMAP copy, never in the wire message.
    if hidden:
        message["Bcc"] = ", ".join(hidden)
    message["Subject"] = subject.strip()
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid()
    if original:
        ids = re.findall(r"<[^<>\s]+@[^<>\s]+>", original["references"] + " " + original["message_id"])[-20:]
        if ids:
            message["In-Reply-To"] = ids[-1]
            message["References"] = " ".join(ids)
    message.set_content(body)
    for filename, content in attachments:
        message.add_attachment(content, maintype="application", subtype="octet-stream", filename=filename)
    return message, envelope


class MailboxService:
    def __init__(self, config=None, smtp=None):
        self.config = config or mailbox_config
        self.smtp = smtp or smtp_config

    @contextmanager
    def connect(self):
        if not self.config.IMAP_HOST:
            raise MailboxError("Skrzynka nie jest jeszcze skonfigurowana. Ustaw IMAP_HOST w Vault lub środowisku.")
        context = ssl.create_default_context()
        client = IMAPClient(
            self.config.IMAP_HOST,
            port=self.config.IMAP_PORT,
            ssl=self.config.IMAP_TLS_MODE == "tls",
            ssl_context=context,
            timeout=self.config.IMAP_TIMEOUT,
        )
        try:
            if self.config.IMAP_TLS_MODE == "starttls":
                client.starttls(ssl_context=context)
            client.login(self.smtp.MAIL_USERNAME, self.smtp.MAIL_PASSWORD.get_secret_value())
            yield client
        finally:
            try:
                client.logout()
            except Exception:
                client.shutdown()

    def folder_list(self, client):
        result = []
        for flags, _, name in client.list_folders():
            if b"\\Noselect" in flags:
                continue
            kind, label = "custom", name
            if name.upper() == "INBOX":
                kind, label = "inbox", "Odebrane"
            for flag, info in SPECIAL_FOLDERS.items():
                if flag in flags:
                    kind, label = info
                    break
            for configured_kind in ("sent", "drafts", "junk", "trash"):
                configured_name = getattr(self.config, "IMAP_" + configured_kind.upper() + "_FOLDER")
                if configured_name and name == configured_name:
                    kind = configured_kind
                    label = dict(SPECIAL_FOLDERS.values())[kind]
            result.append({"name": name, "label": label, "kind": kind})
        return sorted(result, key=lambda f: (f["kind"] != "inbox", f["kind"] == "custom", f["label"]))

    def select(self, client, folder, validity=None, readonly=True):
        if folder not in {item["name"] for item in self.folder_list(client)}:
            raise MailboxError("Folder nie istnieje. Odśwież listę folderów.")
        info = client.select_folder(folder, readonly=readonly)
        current = int(info[b"UIDVALIDITY"])
        if validity is not None and current != validity:
            raise MailboxError("Folder został zmieniony na serwerze. Otwórz ponownie wiadomość z listy.")
        return current

    def special_folder(self, client, kind):
        folders = self.folder_list(client)
        found = next((f["name"] for f in folders if f["kind"] == kind), None)
        if found:
            return found
        configured = getattr(self.config, "IMAP_" + kind.upper() + "_FOLDER")
        if not configured:
            raise MailboxError("Serwer nie wskazał folderu docelowego. Ustaw IMAP_" + kind.upper() + "_FOLDER.")
        if configured not in {f["name"] for f in folders}:
            client.create_folder(configured)
        return configured

    def listing(self, folder="INBOX", page=1, query=""):
        with self.connect() as client:
            folders = self.folder_list(client)
            validity = self.select(client, folder)
            criteria = ["NOT", "DELETED"]
            if query:
                criteria += ["TEXT", query]
            uids = sorted(client.search(criteria, charset="UTF-8"), reverse=True)
            total = len(uids)
            pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(page, pages)
            selected = uids[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
            fetched = (
                client.fetch(selected, ["FLAGS", "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)]"]) if selected else {}
            )
            messages = []
            for uid in selected:
                row = fetched.get(uid)
                if row is None:
                    continue
                raw = next((v for k, v in row.items() if k.startswith(b"BODY[")), b"")
                item = message_data(raw)
                messages.append({**item, "uid": uid, "unread": b"\\Seen" not in row[b"FLAGS"]})
            unread = int(client.folder_status(folder, ["UNSEEN"])[b"UNSEEN"])
            return {
                "folders": folders,
                "messages": messages,
                "validity": validity,
                "page": page,
                "pages": pages,
                "total": total,
                "unread": unread,
            }

    def raw(self, client, folder, uid, validity):
        self.select(client, folder, validity)
        sizes = client.fetch([uid], ["RFC822.SIZE"])
        if uid not in sizes:
            raise MailboxError("Wiadomość nie istnieje lub została przeniesiona.")
        if sizes[uid][b"RFC822.SIZE"] > MAX_MESSAGE_BYTES:
            raise MailboxError("Wiadomość przekracza limit podglądu 25 MB. Otwórz ją w innym kliencie poczty.")
        data = client.fetch([uid], ["BODY.PEEK[]"])
        if uid not in data or b"BODY[]" not in data[uid]:
            raise MailboxError("Nie udało się pobrać wiadomości. Odśwież listę.")
        return data[uid][b"BODY[]"]

    def read(self, folder, uid, validity):
        with self.connect() as client:
            return {
                "message": message_data(self.raw(client, folder, uid, validity)),
                "folders": self.folder_list(client),
            }

    def attachment(self, folder, uid, validity, index):
        with self.connect() as client:
            message = BytesParser(policy=policy.default).parsebytes(self.raw(client, folder, uid, validity))
            for number, part in enumerate(message.walk()):
                if (
                    number == index
                    and not part.is_multipart()
                    and (part.get_filename() or part.get_content_disposition() == "attachment")
                ):
                    return part.get_filename() or "zalacznik", part.get_payload(decode=True) or b""
            raise MailboxError("Nie znaleziono załącznika.")

    def action(self, folder, uid, validity, action, destination=""):
        with self.connect() as client:
            self.select(client, folder, validity, readonly=False)
            if uid not in client.fetch([uid], ["FLAGS"]):
                raise MailboxError("Wiadomość nie istnieje lub została przeniesiona.")
            if action == "read":
                client.add_flags([uid], [b"\\Seen"])
            elif action == "unread":
                client.remove_flags([uid], [b"\\Seen"])
            elif action in {"move", "trash", "junk"}:
                if action != "move":
                    destination = self.special_folder(client, action)
                if destination == folder:
                    raise MailboxError("Wiadomość jest już w tym folderze.")
                if destination not in {f["name"] for f in self.folder_list(client)}:
                    raise MailboxError("Folder docelowy nie istnieje.")
                if not client.has_capability("MOVE"):
                    raise MailboxError("Serwer nie obsługuje IMAP MOVE. Przenieś wiadomość w innym kliencie poczty.")
                client.move([uid], destination)
            else:
                raise MailboxError("Nieznana operacja.")

    def append(self, kind, raw):
        with self.connect() as client:
            folder = self.special_folder(client, kind)
            client.append(folder, raw, flags=[b"\\Seen", b"\\Draft"] if kind == "drafts" else [b"\\Seen"])

    def check_sent_folder(self):
        with self.connect() as client:
            return self.special_folder(client, "sent")
