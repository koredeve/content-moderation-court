# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass
import json


ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_LIVE = "live"
STATUS_REMOVED = "removed"
STATUS_CLEARED = "cleared"

MIN_STAKE = u256(10**17)


def _parse_llm_json(text) -> dict:
	import re
	if isinstance(text, dict):
		return text
	s = str(text)
	first = s.find("{")
	last = s.rfind("}")
	if first == -1 or last <= first:
		raise gl.vm.UserError(f"{ERROR_LLM} no JSON object found in LLM output")
	s = s[first : last + 1]
	s = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", s)
	try:
		parsed = json.loads(s)
	except Exception:
		raise gl.vm.UserError(f"{ERROR_LLM} malformed JSON from LLM")
	if not isinstance(parsed, dict):
		raise gl.vm.UserError(f"{ERROR_LLM} non-dict JSON from LLM")
	return parsed


def _coerce_bool(raw) -> bool:
	if isinstance(raw, bool):
		return raw
	s = str(raw).strip().lower()
	if s in ("true", "1", "yes"):
		return True
	if s in ("false", "0", "no"):
		return False
	raise gl.vm.UserError(f"{ERROR_LLM} non-boolean violation field in LLM output")


def _handle_leader_error(leaders_res, leader_fn) -> bool:
	leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
	try:
		leader_fn()
		return False
	except gl.vm.UserError as e:
		validator_msg = e.message if hasattr(e, "message") else str(e)
		if validator_msg.startswith(ERROR_EXPECTED) or validator_msg.startswith(ERROR_EXTERNAL):
			return validator_msg == leader_msg
		if validator_msg.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT):
			return True
		return False
	except Exception:
		return False


@gl.evm.contract_interface
class _Recipient:
	class View:
		pass

	class Write:
		pass


@allow_storage
@dataclass
class Post:
	author: Address
	content: str
	status: str
	stake_atto: u256
	flag_count: u256
	category: str
	reasoning: str
	flagger: Address
	policy_snapshot: str


class ModerationCourt(gl.Contract):
	owner_addr: Address
	policy_text: str
	posts: TreeMap[str, Post]
	post_ids: DynArray[str]
	credits: TreeMap[Address, u256]

	def __init__(self) -> None:
		self.owner_addr = gl.message.sender_address
		self.policy_text = ""

	def _get_post(self, post_id: str) -> Post:
		post = self.posts.get(post_id)
		if post is None:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Unknown post id")
		return post

	@gl.public.view
	def owner(self) -> str:
		return str(self.owner_addr)

	@gl.public.view
	def get_policy(self) -> str:
		return self.policy_text

	@gl.public.write
	def set_policy(self, text: str) -> None:
		if gl.message.sender_address != self.owner_addr:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Only owner")
		if len(text.strip()) == 0:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Policy text must not be empty")
		self.policy_text = text

	@gl.public.write.payable
	def publish_post(self, post_id: str, content: str) -> None:
		if gl.message.value < MIN_STAKE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Stake below minimum")
		clean_id = str(post_id).strip()
		clean_content = str(content).strip()
		if not clean_id or not clean_content:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Post id and content must not be empty")
		if clean_id in self.posts:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Post id already exists")
		author = gl.message.sender_address
		policy_to_bind = self.policy_text.strip()
		if not policy_to_bind:
			policy_to_bind = "Community Standard: No spam, hate speech, harassment, or malicious content."

		self.posts[clean_id] = Post(
			author=author,
			content=clean_content,
			status=STATUS_LIVE,
			stake_atto=u256(gl.message.value),
			flag_count=u256(0),
			category="",
			reasoning="",
			flagger=author,
			policy_snapshot=policy_to_bind,
		)
		self.post_ids.append(clean_id)

	@gl.public.write
	def flag_post(self, post_id: str) -> None:
		post = self._get_post(post_id)
		if post.status != STATUS_LIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Post is not live")
		sender = gl.message.sender_address
		if str(sender) == str(post.author):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Author cannot flag own post")
		# Invariant: First flagger is permanently preserved; later flags cannot hijack payout recipient
		if post.flag_count == u256(0):
			post.flagger = sender
		post.flag_count = post.flag_count + u256(1)

	@gl.public.write
	def adjudicate(self, post_id: str) -> None:
		post = self._get_post(post_id)
		if post.status != STATUS_LIVE:
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Post is not live")
		if post.flag_count == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Post must be flagged first")
		# Invariant: Staked post is evaluated strictly against its immutable policy snapshot bound at creation
		policy = str(post.policy_snapshot).strip()
		content = str(post.content)

		def leader_fn() -> dict:
			prompt = (
				"You are a content moderation judge.\n"
				f"POLICY: <policy>{policy}</policy>\n"
				f"POST: <post>{content}</post>\n"
				"Does the post violate the policy? Reply JSON "
				'{"violation": true/false, '
				'"category": "spam|hate|harassment|misinformation|none", '
				'"reasoning": "..."}'
			)
			analysis = gl.nondet.exec_prompt(prompt, response_format="json")
			parsed = _parse_llm_json(analysis)
			raw = None
			for key in ("violation", "is_violation"):
				if key in parsed:
					raw = parsed[key]
					break
			if raw is None:
				raise gl.vm.UserError(
					f"{ERROR_LLM} missing violation field in LLM output"
				)
			violation = _coerce_bool(raw)
			category = parsed.get("category", "none")
			reasoning = parsed.get("reasoning", "")
			return {
				"violation": bool(violation),
				"category": str(category),
				"reasoning": str(reasoning),
			}

		def validator_fn(leaders_res: gl.vm.Result) -> bool:
			if not isinstance(leaders_res, gl.vm.Return):
				return _handle_leader_error(leaders_res, leader_fn)
			leader_data = leaders_res.calldata
			fresh = leader_fn()
			return bool(leader_data.get("violation")) == bool(fresh.get("violation"))

		result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

		violation = bool(result["violation"])
		stake = post.stake_atto
		if violation:
			post.status = STATUS_REMOVED
			payee = post.flagger
		else:
			post.status = STATUS_CLEARED
			payee = post.author
		post.category = str(result["category"])
		post.reasoning = str(result["reasoning"])
		self.credits[payee] = self.credits.get(payee, u256(0)) + stake

	@gl.public.write
	def withdraw(self) -> None:
		who = gl.message.sender_address
		amount = self.credits.get(who, u256(0))
		if amount == u256(0):
			raise gl.vm.UserError(f"{ERROR_EXPECTED} Nothing to withdraw")
		self.credits[who] = u256(0)
		_Recipient(who).emit_transfer(value=u256(amount))

	@gl.public.view
	def get_post(self, post_id: str) -> dict:
		post = self._get_post(post_id)
		return {
			"author": str(post.author),
			"flagger": str(post.flagger),
			"content": post.content,
			"status": post.status,
			"stake_atto": post.stake_atto,
			"flag_count": post.flag_count,
			"category": post.category,
			"reasoning": post.reasoning,
			"policy_snapshot": post.policy_snapshot,
		}

	@gl.public.view
	def credit_of(self, who: Address) -> u256:
		return self.credits.get(Address(who), u256(0))

	@gl.public.view
	def total_posts(self) -> u256:
		return u256(len(self.post_ids))
