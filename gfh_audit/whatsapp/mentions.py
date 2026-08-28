"""Mention formatting helpers linking Employee phones to WhatsApp tags."""
from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple

from ..textutils import normalize_phone, person_name_key, whatsapp_mention


class MentionResolver:
    """Resolves rep names from the audit data to @phone tags using the
    Employee directory (mapped in the Employees tab)."""

    def __init__(self, phone_by_name_key: Dict[str, str]):
        self.phone_by_name_key = dict(phone_by_name_key)

    @classmethod
    def from_employees(cls, employees: Iterable[dict]) -> "MentionResolver":
        return cls({person_name_key(e.get("name", "")): e.get("phone", "") for e in employees})

    def phone_for(self, rep_name: str) -> str:
        key = person_name_key(rep_name)
        if not key:
            return ""
        if key in self.phone_by_name_key:
            return normalize_phone(self.phone_by_name_key[key])
        # token overlap fuzzy match
        tokens = set(key.split())
        best_key, best_score = "", 0
        for cand_key, phone in self.phone_by_name_key.items():
            overlap = len(set(cand_key.split()) & tokens)
            if overlap > best_score:
                best_key, best_score = phone, overlap
        return normalize_phone(best_key)

    def mentions_for_rows(self, rep_names: Iterable[str]) -> Tuple[List[str], List[str]]:
        """Return (mention_tags, missing_rep_names) — deduped, ordered."""
        tags: List[str] = []
        missing: List[str] = []
        seen: Set[str] = set()
        for name in rep_names:
            name = (name or "").strip()
            if not name:
                continue
            phone = self.phone_for(name)
            if not phone:
                if name not in missing:
                    missing.append(name)
                continue
            tag = whatsapp_mention(phone)
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
        return tags, missing

    @staticmethod
    def tag_line(tags: Iterable[str], suffix: str) -> str:
        tags = [t for t in tags if t]
        if not tags:
            return ""
        return " ".join(tags) + " " + suffix
