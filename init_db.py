from sqlalchemy import create_engine
from app.config.postgresql_config import postgresql_config
from app.models.base import Base
from app.models.user import User                 # noqa: F401
from app.models.session import Session           # noqa: F401
from app.models.running_chat import RunningChat  # noqa: F401
from app.models.account_verify import AccountVerify  # noqa: F401

engine = create_engine(postgresql_config.url, echo=True)
try:
    Base.metadata.create_all(engine)
finally:
    engine.dispose()
print("已注册的表:", ", ".join(sorted(Base.metadata.tables)))