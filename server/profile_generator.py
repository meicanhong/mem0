import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

PROFILE_MEMORY_MAX_CHARS = int(os.environ.get("MEM0_PROFILE_MEMORY_MAX_CHARS", "1000"))


class ProfileGenerationError(ValueError):
    """Raised when the LLM cannot produce a valid profile payload."""


def generate_profile_payload(llm, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
    prompt = _build_profile_prompt(memories)
    response = llm.generate_response([{"role": "user", "content": prompt}])
    return parse_profile_response(response)


def generate_increase_profile_payload(
    llm,
    current_profile: Dict[str, Any],
    memories: List[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt = _build_increase_profile_prompt(current_profile, memories)
    response = llm.generate_response([{"role": "user", "content": prompt}])
    return parse_profile_response(response)


def _build_profile_prompt(memories: List[Dict[str, Any]]) -> str:
    compact_memories = []
    for memory in memories:
        text = str(memory.get("memory") or "")
        compact_memories.append(
            {
                "id": memory.get("id"),
                "memory": text[:PROFILE_MEMORY_MAX_CHARS],
                "metadata": memory.get("metadata") or {},
                "created_at": memory.get("created_at"),
                "updated_at": memory.get("updated_at"),
            }
        )

    return (
        "You generate a cached user profile from stored long-term memories.\n"
        "Return strict JSON only, with this shape:\n"
        "{\n"
        '  "profile_text": "A concise narrative profile, maximum 3000 characters.",\n'
        '  "profile_json": {\n'
        '    "basic_info": {},\n'
        '    "preferences": [],\n'
        '    "work_context": [],\n'
        '    "stable_facts": [],\n'
        '    "goals": [],\n'
        '    "communication_style": []\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Only include facts supported by the memories.\n"
        "- Prefer stable traits, preferences, background, goals, and communication style.\n"
        "- Do not include one-off events unless they are important ongoing context.\n"
        "- If memories conflict, prefer the most recently updated memory and mention uncertainty only when needed.\n"
        "- Keep profile_text useful as baseline session context.\n\n"
        f"Memories:\n{json.dumps(compact_memories, ensure_ascii=False)}"
    )


def _build_increase_profile_prompt(current_profile: Dict[str, Any], memories: List[Dict[str, Any]]) -> str:
    compact_memories = []
    for memory in memories:
        text = str(memory.get("memory") or "")
        compact_memories.append(
            {
                "id": memory.get("id"),
                "memory": text[:PROFILE_MEMORY_MAX_CHARS],
                "metadata": memory.get("metadata") or {},
                "created_at": memory.get("created_at"),
                "updated_at": memory.get("updated_at"),
            }
        )

    return (
        "You update a cached user profile by incorporating the next batch of long-term memories.\n"
        "Return strict JSON only, with this shape:\n"
        "{\n"
        '  "profile_text": "A concise narrative profile, maximum 3000 characters.",\n'
        '  "profile_json": {\n'
        '    "basic_info": {},\n'
        '    "preferences": [],\n'
        '    "work_context": [],\n'
        '    "stable_facts": [],\n'
        '    "goals": [],\n'
        '    "communication_style": []\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Preserve useful current profile facts unless the new memories contradict them.\n"
        "- Only include facts supported by the current profile or the new memories.\n"
        "- Prefer stable traits, preferences, background, goals, and communication style.\n"
        "- Do not add one-off events unless they are important ongoing context.\n"
        "- Keep profile_text useful as baseline session context.\n\n"
        f"Current profile:\n{json.dumps(current_profile, ensure_ascii=False)}\n\n"
        f"Next memories:\n{json.dumps(compact_memories, ensure_ascii=False)}"
    )


def parse_profile_response(response: str) -> Dict[str, Any]:
    parsed: Dict[str, Any] | None = None
    try:
        parsed = json.loads(remove_code_blocks(response), strict=False)
    except Exception:
        try:
            parsed = json.loads(extract_json(response), strict=False)
        except Exception as exc:
            logger.warning(
                "Profile response was not valid JSON; profile refresh will fail",
                extra={"operation": "profile_refresh", "status": "invalid_llm_response"},
            )
            raise ProfileGenerationError("Profile LLM response was not valid JSON.") from exc

    if not isinstance(parsed, dict):
        logger.warning(
            "Profile response JSON was not an object; profile refresh will fail",
            extra={"operation": "profile_refresh", "status": "invalid_llm_response"},
        )
        raise ProfileGenerationError("Profile LLM response must be a JSON object.")

    profile_text = parsed.get("profile_text")
    profile_json = parsed.get("profile_json")
    if not isinstance(profile_text, str) or not profile_text.strip():
        logger.warning(
            "Profile response missing profile_text; profile refresh will fail",
            extra={"operation": "profile_refresh", "status": "invalid_llm_response"},
        )
        raise ProfileGenerationError("Profile LLM response must include non-empty profile_text.")
    if not isinstance(profile_json, dict):
        logger.warning(
            "Profile response missing profile_json object; profile refresh will fail",
            extra={"operation": "profile_refresh", "status": "invalid_llm_response"},
        )
        raise ProfileGenerationError("Profile LLM response must include profile_json as an object.")
    return {"profile_text": profile_text.strip(), "profile_json": profile_json}


def remove_code_blocks(content: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else content.strip()


def extract_json(content: str) -> str:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in response.")
    return content[start : end + 1]
