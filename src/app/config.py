from fastapi_mail import ConnectionConfig
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal

from vault.vault_settings import *

smtp_config = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_STARTTLS=bool(int(os.getenv("MAIL_STARTTLS", "1"))),
    MAIL_SSL_TLS=bool(int(os.getenv("MAIL_SSL_TLS", "0"))),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    USE_CREDENTIALS=bool(int(os.getenv("USE_CREDENTIALS", "1"))),
)


class MailboxConfig(BaseSettings):
    IMAP_HOST: str = ""
    IMAP_PORT: int = Field(default=993, ge=1, le=65535)
    IMAP_TLS_MODE: Literal["tls", "starttls"] = "tls"
    IMAP_TIMEOUT: int = Field(default=15, ge=1, le=60)
    IMAP_SENT_FOLDER: str = ""
    IMAP_DRAFTS_FOLDER: str = ""
    IMAP_JUNK_FOLDER: str = ""
    IMAP_TRASH_FOLDER: str = ""
    # Disable if the SMTP server already saves a Sent copy automatically.
    IMAP_SAVE_SENT: bool = True


mailbox_config = MailboxConfig(IMAP_HOST=os.getenv("IMAP_HOST", "mail.igor251.mikr.dev"))


class ScheduleMailConfig(BaseSettings):
    MAIL_SUBJECT: str
    MAIL_BODY: str


schedule_mail_config = ScheduleMailConfig(
    MAIL_SUBJECT=f"Aktualizacja planu zajęć - AŚ - {os.getenv("FILE_NAME")}",
    MAIL_BODY="""<p>Cześć, nastąpiła zmiana w planie zajęć.</p>
        <p>Powodzenia!</p>
        <br>
        Plik w załączniku.
        <br><br>
        Jeśli chcesz dodać plan zajęć na swój telefon to wejdź na:
        <a href="https://as.michalwrona.pl/">https://as.michalwrona.pl</a>""",
)
