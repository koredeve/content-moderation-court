import json

STAKE = 2 * 10**18
MIN_STAKE = 10**17

POLICY = "No spam, hate speech, harassment, or misinformation is allowed."

PROMPT_REGEX = r"You are a content moderation judge"

POST_SPAM = "Buy cheap widgets now at example-shop dot com!!!"
POST_CLEAN = "Today I went hiking and enjoyed the mountain view."


def _deploy(direct_vm, direct_deploy, deployer):
    """Deploy with `deployer` as sender; the deployer becomes the owner."""
    direct_vm.sender = deployer
    return direct_deploy("contracts/ModerationCourt.py")


def _publish(direct_vm, contract, author, post_id, content, stake=STAKE):
    direct_vm.sender = author
    direct_vm.value = stake
    contract.publish_post(post_id, content)
    direct_vm.value = 0


def _flag(direct_vm, contract, flagger, post_id):
    with direct_vm.prank(flagger):
        contract.flag_post(post_id)


def _adjudicate(direct_vm, contract, caller, post_id):
    direct_vm.sender = caller
    contract.adjudicate(post_id)


def test_owner_sets_policy_and_non_owner_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Only the owner may store a non-empty policy; it is readable via get_policy."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    assert len(contract.owner()) > 0
    assert contract.get_policy() == ""

    with direct_vm.prank(direct_bob):
        with direct_vm.expect_revert("Only owner"):
            contract.set_policy(POLICY)

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("Policy text must not be empty"):
        contract.set_policy("   ")

    contract.set_policy(POLICY)
    assert contract.get_policy() == POLICY


def test_publish_post_records_state_and_rejects_duplicate_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """A published post stores its author, stake and live status; ids are unique."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    contract.set_policy(POLICY)

    _publish(direct_vm, contract, direct_bob, "post-1", POST_SPAM)

    post = contract.get_post("post-1")
    assert len(post["author"]) > 0
    assert post["content"] == POST_SPAM
    assert post["status"] == "live"
    assert post["stake_atto"] == STAKE
    assert post["flag_count"] == 0
    assert contract.total_posts() == 1

    with direct_vm.expect_revert("Post id already exists"):
        _publish(direct_vm, contract, direct_bob, "post-1", POST_SPAM)
    assert contract.total_posts() == 1


def test_publish_below_min_stake_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Publishing with a stake under MIN_STAKE (10**17) is rejected."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)

    with direct_vm.expect_revert("Stake below minimum"):
        _publish(
            direct_vm, contract, direct_bob, "post-low", POST_SPAM, stake=MIN_STAKE - 1
        )
    assert contract.total_posts() == 0


def test_author_cannot_flag_own_post(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """The author of a live post cannot flag it; others can."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _publish(direct_vm, contract, direct_alice, "post-1", POST_CLEAN)

    with direct_vm.prank(direct_alice):
        with direct_vm.expect_revert("Author cannot flag own post"):
            contract.flag_post("post-1")

    with direct_vm.expect_revert("Unknown post id"):
        contract.flag_post("missing-post")

    _flag(direct_vm, contract, direct_bob, "post-1")
    assert contract.get_post("post-1")["flag_count"] == 1


def test_adjudicate_requires_flag_first_and_known_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Adjudication is only allowed for a known, live, already-flagged post."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    _publish(direct_vm, contract, direct_bob, "post-1", POST_CLEAN)

    with direct_vm.expect_revert("Unknown post id"):
        contract.adjudicate("missing-post")

    with direct_vm.expect_revert("Post must be flagged first"):
        contract.adjudicate("post-1")
    assert contract.get_post("post-1")["status"] == "live"


def test_flagged_spam_is_removed_and_flagger_gets_stake(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """A flagged post judged as violating is removed and the flagger wins the stake."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    contract.set_policy(POLICY)

    _publish(direct_vm, contract, direct_charlie, "post-1", POST_SPAM)
    _flag(direct_vm, contract, direct_bob, "post-1")

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"violation": True, "category": "spam", "reasoning": "unsolicited ads"}),
    )
    _adjudicate(direct_vm, contract, direct_alice, "post-1")

    post = contract.get_post("post-1")
    assert post["status"] == "removed"
    assert post["category"] == "spam"
    assert post["reasoning"] == "unsolicited ads"
    assert contract.credit_of(direct_bob) == STAKE
    assert contract.credit_of(direct_charlie) == 0


def test_flagged_clean_post_is_cleared_and_author_refunded(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """A flagged post judged as compliant is cleared and the author's stake is returned."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    contract.set_policy(POLICY)

    _publish(direct_vm, contract, direct_bob, "post-2", POST_CLEAN)
    _flag(direct_vm, contract, direct_charlie, "post-2")

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"violation": False, "category": "none", "reasoning": "innocuous diary entry"}),
    )
    _adjudicate(direct_vm, contract, direct_alice, "post-2")

    post = contract.get_post("post-2")
    assert post["status"] == "cleared"
    assert post["category"] == "none"
    assert contract.credit_of(direct_bob) == STAKE
    assert contract.credit_of(direct_charlie) == 0


def test_double_adjudicate_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """A post that has already been adjudicated cannot be adjudicated again."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    contract.set_policy(POLICY)

    _publish(direct_vm, contract, direct_charlie, "post-1", POST_SPAM)
    _flag(direct_vm, contract, direct_bob, "post-1")

    direct_vm.mock_llm(
        PROMPT_REGEX,
        json.dumps({"violation": True, "category": "spam", "reasoning": "ads"}),
    )
    _adjudicate(direct_vm, contract, direct_alice, "post-1")

    with direct_vm.expect_revert("Post is not live"):
        contract.adjudicate("post-1")

    assert contract.credit_of(direct_bob) == STAKE
    assert contract.credit_of(direct_charlie) == 0


def test_malformed_llm_output_raises_user_error(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """Unparseable LLM output surfaces as an [LLM_ERROR] UserError and leaves the post live."""
    contract = _deploy(direct_vm, direct_deploy, direct_alice)
    direct_vm.sender = direct_alice
    contract.set_policy(POLICY)

    _publish(direct_vm, contract, direct_charlie, "post-1", POST_SPAM)
    _flag(direct_vm, contract, direct_bob, "post-1")

    direct_vm.mock_llm(PROMPT_REGEX, "Sorry, I cannot judge that.")
    with direct_vm.expect_revert("[LLM_ERROR]"):
        _adjudicate(direct_vm, contract, direct_alice, "post-1")

    assert contract.get_post("post-1")["status"] == "live"
    assert contract.credit_of(direct_bob) == 0
