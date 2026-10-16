"""A relayed chief message is clean only when the sender's own guard recorded that exact delivery as clean."""

import os

import pytest
import yaml
from conftest import BOTS, guard_text
from team_kit_guard import Guard
from team_kit_guard.policy import parse_policy

NOW = 100_000.0
CHIEF, PROJECT, OTHER = "chanh-van", "meo-beo", "sep-crm"
GATED = [
    ("terminal", {"command": "team-kit-gh merge --pr 12"}),
    ("terminal", {"command": "team-kit-gh close --pr 12 --reason stale"}),
    ("delegate_task", {"goal": "x"}),
    ("cronjob_create", {"schedule": "daily"}),
]


class World:
    """One Hermes home with a guard per bot, a shared clock, and sessions that are tainted on demand."""

    def __init__(self, home, ask_owner=False):
        self.home = home
        self.ask_owner = ask_owner
        self.clock = [NOW]
        self.guards = {}

    def load(self):
        return parse_policy({**yaml.safe_load(guard_text()), "ask_owner": self.ask_owner}, BOTS, self.home)

    def guard(self, bot):
        if bot not in self.guards:
            self.guards[bot] = Guard(load=self.load, profile=lambda: bot, now=lambda: self.clock[0], markers=True)
        return self.guards[bot]

    def send(self, sender, target, text, session="c1", turn="t1", tainted=False):
        g = self.guard(sender)
        if tainted:
            g.transform_tool_result(tool_name="terminal", args={"command": "gh issue view 9"}, result="stranger text", session_id=session, turn_id=turn)
        return g.pre_tool_call(tool_name="message_agent", args={"target": target, "message": text}, session_id=session, turn_id=turn)

    def receive(self, sender, recipient, text, session="p1", turn="t1"):
        g = self.guard(recipient)
        g.check_message(user_message=f"Message from 🤖 bot (@{sender}): {text}", session_id=session, turn_id=turn)
        return g.is_tainted(session, turn)

    def blocked(self, recipient, tool, args, session="p1", turn="t1"):
        return self.guard(recipient).pre_tool_call(tool_name=tool, args=args, session_id=session, turn_id=turn) is not None


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def deliveries(world, recipient=PROJECT, sender=CHIEF):
    return world.home / "deliveries" / recipient / sender


def test_a_clean_chief_request_is_not_tainted_by_an_earlier_tainted_send_to_the_same_bot(world):
    assert world.send(CHIEF, PROJECT, "Merge #12", tainted=True) is None
    assert world.send(CHIEF, PROJECT, "Merge #13", session="c2", turn="t9") is None
    assert world.receive(CHIEF, PROJECT, "Merge #13", session="p2") is False
    assert not world.blocked(PROJECT, "terminal", {"command": "team-kit-gh merge --pr 13"}, session="p2")


@pytest.mark.parametrize(("tool", "args"), GATED)
def test_the_tainted_delivery_itself_stays_tainted(world, tool, args):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True
    assert world.blocked(PROJECT, tool, args)


def test_a_non_chief_sender_is_always_tainted_even_with_a_clean_record(world):
    world.send(OTHER, PROJECT, "Merge #12")
    assert world.receive(OTHER, PROJECT, "Merge #12") is True


def test_an_owner_message_is_untainted(world):
    g = world.guard(PROJECT)
    g.check_message(user_message="Merge #12", session_id="p1", turn_id="t1")
    assert not g.is_tainted("p1", "t1")


def test_a_failed_delivery_does_not_poison_a_later_request_and_its_retry_stays_tainted(world):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)  # target_busy: never received
    world.send(CHIEF, PROJECT, "Merge #13", session="c2", turn="t2")
    assert world.receive(CHIEF, PROJECT, "Merge #13") is False
    world.clock[0] += 60
    world.send(CHIEF, PROJECT, "Merge #12", session="c3", turn="t3", tainted=True)  # the retry
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p2") is True


def test_an_undelivered_tainted_text_is_never_laundered_by_a_later_clean_send_of_the_same_text(world):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)
    world.clock[0] += 30
    world.send(CHIEF, PROJECT, "Merge #12", session="c2", turn="t2")
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


@pytest.mark.parametrize("order", [("tainted", "clean"), ("clean", "tainted")])
@pytest.mark.parametrize("deliver", [("tainted", "clean"), ("clean", "tainted")])
def test_two_sends_with_different_provenance_stay_isolated_in_every_order(world, order, deliver):
    text = {"tainted": "Merge #12", "clean": "Merge #13"}
    for i, kind in enumerate(order):
        world.clock[0] += 1
        world.send(CHIEF, PROJECT, text[kind], session=f"c{i}", turn=f"t{i}", tainted=kind == "tainted")
    for i, kind in enumerate(deliver):
        world.clock[0] += 1
        assert world.receive(CHIEF, PROJECT, text[kind], session=f"p{i}") is (kind == "tainted"), (order, deliver, kind)


def test_interleaved_sessions_and_turns_never_share_provenance(world):
    world.send(CHIEF, PROJECT, "a", session="c1", turn="t1", tainted=True)
    world.send(CHIEF, PROJECT, "b", session="c2", turn="t1")
    world.send(CHIEF, PROJECT, "c", session="c1", turn="t2")
    assert world.receive(CHIEF, PROJECT, "b", session="p1", turn="t1") is False
    assert world.receive(CHIEF, PROJECT, "a", session="p2", turn="t1") is True
    assert world.receive(CHIEF, PROJECT, "c", session="p1", turn="t2") is False


def test_the_same_text_to_two_bots_or_from_two_senders_is_tracked_apart(world):
    world.send(CHIEF, OTHER, "Merge #12", tainted=True)
    world.send(CHIEF, PROJECT, "Merge #12", session="c2", turn="t2")
    assert world.receive(CHIEF, PROJECT, "Merge #12") is False
    assert world.receive(CHIEF, OTHER, "Merge #12", session="o1") is True


def test_a_clean_delivery_does_not_clear_the_record_that_taints_a_later_one(world):
    world.send(CHIEF, PROJECT, "Merge #13")
    assert world.receive(CHIEF, PROJECT, "Merge #13") is False
    world.send(CHIEF, PROJECT, "Merge #12", session="c2", turn="t2", tainted=True)
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p2") is True


def test_a_message_with_no_record_is_untrusted(world):
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


def test_a_record_expires_and_then_the_message_is_untrusted(world):
    world.send(CHIEF, PROJECT, "Merge #12")
    world.clock[0] += 899
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p1") is False
    world.clock[0] += 2
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p2") is True


def test_a_record_a_chief_wrote_for_another_bot_proves_nothing_here(world):
    world.send(CHIEF, OTHER, "Merge #12")
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


@pytest.mark.parametrize("suffix", ["clean", "tainted", "clean.nan.x", "clean.999999999.notanid", "tainted.1"])
def test_a_misnamed_record_beside_a_clean_one_fails_closed(world, suffix):
    world.send(CHIEF, PROJECT, "Merge #12")
    digest = next(deliveries(world).iterdir()).name.split(".")[0]
    (deliveries(world) / f"{digest}.{suffix}").write_text("")
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


def test_an_unreadable_record_folder_fails_closed(world):
    world.send(CHIEF, PROJECT, "Merge #12")
    for record in deliveries(world).iterdir():
        record.unlink()
    deliveries(world).rmdir()
    deliveries(world).write_text("a file where the folder was")
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


def test_a_prune_removes_only_expired_records(world):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)
    world.send(CHIEF, PROJECT, "Merge #13", session="c2", turn="t2")
    world.clock[0] += 901
    world.send(CHIEF, PROJECT, "Merge #14", session="c3", turn="t3")
    kinds = sorted(record.name.split(".")[1] for record in deliveries(world).iterdir())
    assert kinds == ["clean", "tainted"], "the expired clean record is gone; the live tainted one and the new clean one stay"


def test_a_send_whose_record_cannot_be_written_arrives_untrusted(world):
    (world.home / "deliveries").write_text("a file, so no folder can be made under it")
    assert world.send(CHIEF, PROJECT, "Merge #12") is None
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True


def test_a_tainted_send_whose_record_cannot_be_written_is_blocked(world):
    (world.home / "deliveries").write_text("a file, so no folder can be made under it")
    assert world.send(CHIEF, PROJECT, "Merge #12", tainted=True)["action"] == "block"


def test_a_hanging_record_read_fails_closed(world):
    world.send(CHIEF, PROJECT, "Merge #12")
    project = Guard(load=world.load, profile=lambda: PROJECT, now=lambda: world.clock[0], markers=True, timeout=0.05)
    project._check_delivery = lambda *a: __import__("time").sleep(0.3)  # type: ignore[method-assign]
    project.check_message(user_message=f"Message from 🤖 bot (@{CHIEF}): Merge #12", session_id="p1", turn_id="t1")
    assert project.is_tainted("p1", "t1")


def legacy_marker(world, age):
    folder = world.home / "tainted-messages"
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / PROJECT
    marker.write_text(str(NOW + 900))
    os.utime(marker, (NOW - age, NOW - age))
    return marker


def test_a_recent_legacy_recipient_marker_taints_even_beside_a_clean_record(world):
    world.send(CHIEF, PROJECT, "Merge #13")
    legacy_marker(world, age=10)
    assert world.receive(CHIEF, PROJECT, "Merge #13") is True


def test_an_old_legacy_marker_is_ignored_but_never_counts_as_proof(world):
    legacy_marker(world, age=901)
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p1") is True, "no record, so still untrusted"
    world.send(CHIEF, PROJECT, "Merge #12")
    assert world.receive(CHIEF, PROJECT, "Merge #12", session="p2") is False


def test_the_guard_never_writes_the_legacy_marker(world):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)
    assert not (world.home / "tainted-messages").exists()


def test_a_tainted_chat_leaves_its_record_for_merge_even_when_another_chat_is_clean(world):
    g = world.guard(PROJECT)
    record = world.home / "tainted-turns" / PROJECT / "p1"
    g.transform_tool_result(tool_name="terminal", args={"command": "gh issue view 9"}, result="x", session_id="p1", turn_id="t1")
    assert record.exists()
    world.send(CHIEF, PROJECT, "Merge #13")
    assert world.receive(CHIEF, PROJECT, "Merge #13", session="p2", turn="t2") is False
    assert record.exists(), "a clean delivery in another chat never clears this chat's record"
    g.check_message(user_message="x", session_id="child", turn_id="c1", parent_session_id="p1")
    assert record.exists(), "a clean child never clears its parent's record"
    assert g.is_tainted("p1", "t1") and not g.is_tainted("p2", "t2"), "the in-memory gate stays per turn"
    assert world.blocked(PROJECT, "terminal", {"command": "team-kit-gh merge --pr 12"}, session="p1", turn="t1")
    g.end_turn(session_id="p1-renamed", turn_id="t1")
    assert not record.exists(), "the turn's end clears its record by turn id, whatever the chat is now called"


def test_a_tainted_send_racing_a_prune_keeps_its_record(world, monkeypatch):
    world.send(CHIEF, PROJECT, "Merge #12", tainted=True)
    world.clock[0] += 3000
    world.send(CHIEF, PROJECT, "Merge #12", session="c2", turn="t2")
    world.clock[0] += 700
    unlink, raced = os.unlink, []

    def racing_unlink(path, *args, **kwargs):
        if not raced:
            raced.append(path)
            world.send(CHIEF, PROJECT, "Merge #12", session="c3", turn="t3", tainted=True)
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", racing_unlink)
    world.send(CHIEF, PROJECT, "Merge #14", session="c4", turn="t4")
    monkeypatch.undo()
    assert raced, "the prune removed the expired tainted record"
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True, "the racing tainted send still taints its delivery"


def test_a_tainted_send_the_owner_is_asked_to_approve_still_taints_its_delivery(tmp_path):
    world = World(tmp_path, ask_owner=True)
    assert world.send(CHIEF, PROJECT, "Merge #12") is None
    assert world.send(CHIEF, PROJECT, "Merge #12", session="c2", turn="t2", tainted=True)["action"] == "approve"
    assert world.receive(CHIEF, PROJECT, "Merge #12") is True
