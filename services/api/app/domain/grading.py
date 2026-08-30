from __future__ import annotations


def grade_answer(question_type: str, expected: str, given: str) -> bool:
    if question_type == "short":
        normalized_given = "".join(given.split()).lower()
        normalized_expected = "".join(expected.split()).lower()
        alternatives = [alternative.strip() for alternative in expected.split("|")]
        return normalized_given == normalized_expected or any(
            normalized_given == "".join(alternative.split()).lower() for alternative in alternatives
        )
    return given.strip().upper() == expected.strip().upper()
