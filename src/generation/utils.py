"""Shared utilities for LLM response processing."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def get_response_text(response: Any) -> str:
    """Extract text from a Gemini response, skipping thinking/signature parts.

    When thinking_config is enabled, response.text triggers a warning about
    non-text parts (thought, thought_signature).  This helper accesses the
    parts directly and concatenates only the text ones.
    """
    if response.candidates and response.candidates[0].content:
        text_parts = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text is not None:
                text_parts.append(part.text)
        if text_parts:
            return "".join(text_parts)
    # Fallback: let the SDK handle it (may warn, but at least doesn't crash)
    return response.text


def extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences and trailing text.

    The model sometimes outputs valid JSON followed by extra commentary or
    duplicate JSON blocks (especially with high thinking budgets).  This
    helper tries several strategies:

    1. Strip markdown fences and parse.
    2. If that fails with "Extra data", use json.JSONDecoder to parse only
       the first complete JSON object and ignore the rest.
    3. Regex-extract the first ``{...}`` block (greedy, brace-balanced).
    """
    # Strip markdown code fences if present
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Strategy 1: plain parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_err:
        # Strategy 2: parse only the first JSON value (ignores trailing data)
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(cleaned)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: find the first brace-balanced {...} substring
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(cleaned)):
                ch = cleaned[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start : i + 1])
                        except json.JSONDecodeError:
                            break

        # Nothing worked — raise the original error
        raise first_err
