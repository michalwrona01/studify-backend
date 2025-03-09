from sqlalchemy.orm import Mapped, DeclarativeBase
from sqlalchemy.testing.schema import mapped_column


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)