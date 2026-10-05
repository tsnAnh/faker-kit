import pytest
from team_kit_risk import (
    RISK_MARKER,
    Risk,
    codeowners_match,
    is_repair_commit,
    normalize_risk_overrides,
    owners_for,
    parse_codeowners,
    pick_reviewers,
    score,
    unowned_paths,
)


def files(*entries):
    return [{"filename": f, "status": st, "additions": a, "deletions": d} for f, st, a, d in entries]


@pytest.mark.parametrize(
    ("changed", "verification", "level", "reason"),
    [
        (files(("src/a.py", "modified", 10, 2)), "live-ui-verified", "low", None),
        (files(("src/a.py", "modified", 150, 50)), "live-ui-verified", "medium", "200 lines changed"),
        (files(("src/a.py", "modified", 700, 0)), "live-ui-verified", "high", "700 lines changed"),
        (files(("app/auth/login.py", "modified", 3, 1)), "live-ui-verified", "high", "touches app/auth/login.py"),
        (files((".github/workflows/ci.yml", "modified", 1, 1)), "live-ui-verified", "high", "touches .github/workflows/ci.yml"),
        (files(("infra/main.tf", "modified", 1, 1)), "live-ui-verified", "high", "touches infra/main.tf"),
        (files(("package.json", "modified", 1, 1)), "live-ui-verified", "medium", "touches package.json"),
        (files(("web/Dockerfile", "modified", 1, 1)), "live-ui-verified", "medium", "touches web/Dockerfile"),
        (files(("tests/test_cart.py", "removed", 0, 40)), "live-ui-verified", "medium", "removes tests: tests/test_cart.py"),
        (files(("src/cart.test.ts", "modified", 2, 40)), "live-ui-verified", "medium", "tests shrink by 38 lines"),
        (files(("src/a.py", "modified", 1, 1)), "type-check-only", "medium", "verified by type-check only"),
        (files(("src/a.py", "modified", 1, 1)), None, "medium", "not independently verified"),
        (files(*[(f"src/f{i}.py", "modified", 1, 0) for i in range(30)]), "live-ui-verified", "medium", "30 files changed"),
    ],
)
def test_the_score_is_the_highest_factor_and_says_why(changed, verification, level, reason):
    risk = score(changed, verification)
    assert risk.level == level
    assert (reason in risk.reasons) if reason else risk.reasons == ()


def test_a_high_path_is_not_listed_again_as_medium_and_extra_paths_count():
    risk = score(files((".github/workflows/ci.yml", "modified", 1, 1), ("apps/ledger/x.go", "modified", 1, 1)), "unit-test-verified",
                 high_paths=("apps/ledger/",))
    assert risk.level == "high"
    assert risk.reasons == ("touches .github/workflows/ci.yml, apps/ledger/x.go",)


def test_recent_revert_or_hotfix_history_is_medium_risk():
    risk = score(files(("src/cart.py", "modified", 1, 1)), "unit-test-verified", recent_repair_paths=("src/cart.py",))
    assert risk.level == "medium"
    assert risk.reasons == ("recent revert or hotfix touched src/cart.py",)
    assert is_repair_commit({"commit": {"message": "Revert \"checkout tax rounding\""}})
    assert is_repair_commit({"commit": {"message": "Hot-fix production checkout crash"}})
    assert not is_repair_commit({"commit": {"message": "Polish checkout copy"}})


def test_risk_overrides_add_repository_rules_and_are_validated():
    overrides = normalize_risk_overrides([{"level": "medium", "paths": ["apps/mobile/"], "reason": "mobile release surface"}])
    risk = score(files(("apps/mobile/paywall.swift", "modified", 4, 1)), "unit-test-verified", risk_overrides=overrides)
    assert risk.level == "medium"
    assert risk.reasons == ("mobile release surface: apps/mobile/paywall.swift",)
    with pytest.raises(ValueError, match="risk_overrides\\[1\\].level"):
        normalize_risk_overrides([{"level": "urgent", "paths": ["apps/mobile/"]}])


def test_codeowners_last_matching_rule_wins_and_counts_order_reviewers():
    rules = parse_codeowners("# owners\n* @root\n/apps/api/ @ana @org/api-team\n*.sql @dana\ndocs/** @wen  # docs\n")
    assert rules[0] == ("*", ("root",))
    assert owners_for(["apps/api/db/x.sql", "apps/api/views.py", "apps/api/urls.py", "docs/a/b.md", "README.md"], rules) == \
        ["ana", "org/api-team", "dana", "root", "wen"]
    assert codeowners_match("lib/apps/api/x.py", "apps/api/") and not codeowners_match("lib/apps/api/x.py", "/apps/api/")


def test_codeowners_unowned_paths_are_medium_risk():
    rules = parse_codeowners("docs/ @wen\n")
    assert unowned_paths(["docs/guide.md", "src/cart.py"], rules) == ["src/cart.py"]
    risk = score(files(("src/cart.py", "modified", 1, 1)), "unit-test-verified", codeowners_unowned_paths=("src/cart.py",))
    assert risk.level == "medium"
    assert risk.reasons == ("CODEOWNERS has no owner for src/cart.py",)


def test_reviewers_skip_the_owner_and_bots_and_cap_at_three():
    assert pick_reviewers(["Codertocat", "lan", "LAN", "minh", "", "huy", "tuan"], {"codertocat"}) == ("lan", "minh", "huy")


def test_the_comment_says_it_is_rules_and_never_a_review():
    text = Risk("high", ("touches infra/main.tf",), ("lan",)).comment()
    assert text.startswith(f"{RISK_MARKER}\nrisk: high\nsuggested reviewers: @lan\n")
    assert "no model" in text and "never approves or requests a review" in text
