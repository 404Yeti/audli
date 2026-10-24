"""AUD-87: skip the pool pre-ping for a connection that was in use moments ago (AUDLI_DB_LAZY_PING, default off).

SQLAlchemy's ``pool_pre_ping`` sends a round trip before EVERY checkout; at 72 ms to Tokyo that is ~150 pings (~11 s) per lesson,
of which every one inside an active lesson is wasted (the connection was returned to the pool milliseconds earlier). With the flag
on, a ping is sent only when the connection has been idle for more than ``max_idle_seconds``; it still catches connections that
died while the pool sat idle. The residual risk is a connection that dies inside the window: its first statement fails (the
request fails once and SQLAlchemy discards the connection), exactly as it would for a connection that dies mid-request today.
"""
from time import monotonic

from sqlalchemy import event

MARK = '_audli_last_checkin'


def install_lazy_ping(engine, max_idle_seconds: float = 30.0) -> None:
    if getattr(engine, '_audli_lazy_ping', False):
        return
    engine._audli_lazy_ping = True
    dialect = engine.dialect
    original = dialect.do_ping

    @event.listens_for(engine, 'checkin')
    def returned(dbapi_connection, record):
        try:
            setattr(dbapi_connection, MARK, monotonic())
        except AttributeError:        # a driver object that refuses attributes simply keeps pinging
            pass

    def do_ping(dbapi_connection):
        since = getattr(dbapi_connection, MARK, None)
        if since is not None and monotonic() - since < max_idle_seconds:
            return True
        return original(dbapi_connection)
    dialect.do_ping = do_ping
