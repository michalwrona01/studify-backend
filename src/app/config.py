from fastapi_mail import ConnectionConfig
from pydantic_settings import BaseSettings

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


class ScheduleMailConfig(BaseSettings):
    MAILS_TO: str
    MAIL_SUBJECT: str
    MAIL_BODY: str


schedule_mail_config = ScheduleMailConfig(
    MAILS_TO=os.getenv("MAILS_TO"),
    MAIL_SUBJECT="Aktualizacja planu zajęć - AŚ - Lekarski semestr 7",
    MAIL_BODY="""<p>Witaj, nastąpiła zmiana w planie zajęć.</p>
        <p>Powodzenia!</p>
        <br><br>
        Plik w załączniku.
        <br><br>
        Jeśli chcesz dodać plan zajęć na swój telefon to wejdź na:
        <a href="https://as.wronamichal.pl/">https://as.wronamichal.pl</a>""",
)
