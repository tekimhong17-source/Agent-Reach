"""Tests for the manual plan-grant CLI (rescuing a missed webhook)."""

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CARDVAULT_DB", str(tmp_path / "grant.db"))
    import backend.database as database
    importlib.reload(database)
    database.init_db()
    return database


@pytest.fixture()
def grant(db):
    import backend.grant as grant_mod
    importlib.reload(grant_mod)
    return grant_mod


def make_user(db, email="buyer@example.com"):
    return db.create_user(email, "pbkdf2_sha256$1$aa$bb")


def test_grants_pro(db, grant, capsys):
    uid = make_user(db)
    assert grant.main(["buyer@example.com", "pro", "--yes"]) == 0
    assert db.get_user(uid)["plan"] == "pro"
    assert "now on pro" in capsys.readouterr().out


def test_grants_lifetime(db, grant):
    uid = make_user(db)
    assert grant.main(["buyer@example.com", "lifetime", "--yes"]) == 0
    assert db.get_user(uid)["plan"] == "lifetime"


def test_revokes_to_free(db, grant):
    uid = make_user(db)
    db.set_plan(uid, "pro")
    assert grant.main(["buyer@example.com", "free", "--yes"]) == 0
    assert db.get_user(uid)["plan"] == "free"


def test_email_is_case_insensitive(db, grant):
    uid = make_user(db, "Buyer@Example.com")
    assert grant.main(["buyer@example.com", "pro", "--yes"]) == 0
    assert db.get_user(uid)["plan"] == "pro"


def test_unknown_email_fails_without_changing_anything(db, grant, capsys):
    uid = make_user(db)
    assert grant.main(["nobody@example.com", "pro", "--yes"]) == 1
    assert db.get_user(uid)["plan"] == "free"
    assert "No account" in capsys.readouterr().err


def test_already_on_plan_is_a_no_op(db, grant, capsys):
    uid = make_user(db)
    db.set_plan(uid, "lifetime")
    assert grant.main(["buyer@example.com", "lifetime", "--yes"]) == 0
    assert "nothing to do" in capsys.readouterr().out


def test_invalid_plan_rejected(db, grant):
    with pytest.raises(SystemExit) as e:
        grant.main(["buyer@example.com", "platinum", "--yes"])
    assert e.value.code == 2


def test_missing_arguments_rejected(db, grant):
    with pytest.raises(SystemExit) as e:
        grant.main(["buyer@example.com"])
    assert e.value.code == 2


def test_refuses_without_confirmation_when_not_a_tty(db, grant, monkeypatch, capsys):
    uid = make_user(db)
    monkeypatch.setattr("builtins.input", lambda *_a: (_ for _ in ()).throw(EOFError()))
    assert grant.main(["buyer@example.com", "pro"]) == 1
    assert db.get_user(uid)["plan"] == "free"
    assert "--yes" in capsys.readouterr().err


def test_declining_the_prompt_changes_nothing(db, grant, monkeypatch):
    uid = make_user(db)
    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    assert grant.main(["buyer@example.com", "pro"]) == 1
    assert db.get_user(uid)["plan"] == "free"


def test_warns_when_granting_pro_without_a_subscription_id(db, grant, capsys):
    make_user(db)
    grant.main(["buyer@example.com", "pro", "--yes"])
    assert "cancellation will NOT downgrade" in capsys.readouterr().out


def test_rescued_subscription_can_later_be_downgraded(db, grant):
    """The whole point of --subscription-id: cancellation must still work."""
    uid = make_user(db)
    assert grant.main(
        ["buyer@example.com", "pro", "--subscription-id", "sub_777", "--yes"]) == 0
    assert db.get_user(uid)["subscription_id"] == "sub_777"

    # the eventual subscription_ended webhook looks the user up by that id
    found = db.get_user_by_subscription("sub_777")
    assert found is not None and found["id"] == uid
    db.set_plan(found["id"], "free")
    assert db.get_user(uid)["plan"] == "free"


def test_list_shows_accounts(db, grant, capsys):
    make_user(db, "a@example.com")
    uid_b = make_user(db, "b@example.com")
    db.set_plan(uid_b, "lifetime")
    assert grant.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "a@example.com" in out and "b@example.com" in out
    assert "lifetime" in out


def test_list_on_empty_database(db, grant, capsys):
    assert grant.main(["--list"]) == 0
    assert "No accounts yet" in capsys.readouterr().out
