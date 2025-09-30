from fastapi_mail import ConnectionConfig

from vault.vault_settings import *

smtp_settings = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    MAIL_PORT=int(os.getenv("MAIL_PORT")),
    MAIL_STARTTLS=bool(int(os.getenv("MAIL_STARTTLS", "1"))),
    MAIL_SSL_TLS=bool(int(os.getenv("MAIL_SSL_TLS", "0"))),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    USE_CREDENTIALS=bool(int(os.getenv("USE_CREDENTIALS", "1"))),
)
