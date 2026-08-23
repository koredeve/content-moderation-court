# Content Moderation Court

A GenLayer smart contract implementing community content moderation with economic skin in the game. Authors stake GEN to publish posts; community members flag posts they believe violate the owner's published policy; AI validators judge the flagged content against that policy. If the post is found violating, the author's stake is awarded to the flagger; if the post is compliant, the stake is returned (credited) to the author.

## Architecture

- **User action**: An author publishes a post (`publish_post`) with a stake of at least `MIN_STAKE` (10^17 atto). Anyone except the author can `flag_post` a live post. The owner sets the moderation policy via `set_policy`.
- **Evidence source**: The on-chain policy text and the post content are the only evidence used for judging.
- **Nondet call**: `adjudicate` runs a leader function that calls `gl.nondet.exec_prompt(..., response_format="json")` asking an LLM to compare the post against the policy and reply with JSON `{"violation": bool, "category": str, "reasoning": str}`.
- **Equivalence principle**: A custom validator reruns the identical leader prompt independently and accepts only on **exact agreement of the `violation` boolean** between leader and validator reruns (category/reasoning are informational and not compared). Leader failures are reconciled via the canonical `_handle_leader_error` handler so deterministic `[EXPECTED]`/`[EXTERNAL]` errors reproduce consistently.
- **Settlement effect**: On violation the post becomes `removed` and the full stake is credited to the flagger; otherwise it becomes `cleared` and the stake is credited back to the author. The category and reasoning are persisted on the post. Credits are withdrawable via `withdraw`.
- **Appeal path**: GenLayer Optimistic Democracy provides leader-proposes / validator-check with an appeal window natively; no extra appeal logic is required at the contract level.

## Quickstart

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Lint the contract
/Users/mac/Documents/Default\ Project/.venv/bin/genvm-lint check contracts/ModerationCourt.py --json

# Run direct-mode tests
/Users/mac/Documents/Default\ Project/.venv/bin/pytest tests/direct/ -v
```

## Interface

| Method | Type | Notes |
| --- | --- | --- |
| `owner()` | view | Contract owner (deployer), as string |
| `get_policy()` | view | Current moderation policy text |
| `set_policy(text)` | write | Owner-only; non-empty text |
| `publish_post(post_id, content)` | write, payable | Requires value >= MIN_STAKE (10^17); unique id; starts `live` |
| `flag_post(post_id)` | write | Live posts only; author cannot flag own post; tracks latest flagger |
| `adjudicate(post_id)` | write | Requires live + flagged; AI judge decides violation vs policy |
| `withdraw()` | write | Withdraws accrued credits |
| `get_post(post_id)` | view | Post state incl. author/flagger as strings, status, stake, verdict |
| `credit_of(who)` | view | Withdrawable credit balance for an address |
| `total_posts()` | view | Number of published posts |

## StudioNet

StudioNet is gasless — deploying and interacting costs 0 GEN, so the payable stake in tests is purely contract-level accounting.
