import pytest

from app.services.admin_service import create_admin, deactivate_admin, reset_admin_password


def test_deactivate_and_reset():
    from app.db.database import Base, SessionLocal, engine
    Base.metadata.create_all(bind=engine)
    db_session = SessionLocal()
    from app.db.models import AdminUser
    db_session.query(AdminUser).update({AdminUser.is_active: False})   # isolate from admins other tests created
    db_session.commit()
    a = create_admin(db_session, "ma@x.com", "longpassword1")
    with pytest.raises(ValueError):
        deactivate_admin(db_session, "ma@x.com")          # last active admin
    create_admin(db_session, "mb@x.com", "longpassword2")
    assert deactivate_admin(db_session, "ma@x.com").is_active is False
    with pytest.raises(ValueError):
        reset_admin_password(db_session, "mb@x.com", "short")
    assert reset_admin_password(db_session, "ma@x.com", "newpassword12").is_active is True
    with pytest.raises(ValueError):
        deactivate_admin(db_session, "nobody@x.com")
