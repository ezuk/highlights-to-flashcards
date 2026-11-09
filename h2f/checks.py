"""Card checks, surfaced as `warn` on a card."""

from . import cardgen

DUPLICATE_OVERLAP = 0.7
DUPLICATE_WARNING = "Asks nearly the same thing as another card."


def overlap(first, second):
    """Jaccard overlap of the two texts' word sets, from 0 to 1."""
    a, b = set(cardgen.words(first).split()), set(cardgen.words(second).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _comparable_text(card):
    # Cloze cards on one highlight quote the same text, so only their hidden words can repeat
    front = card.get("front") or ""
    if card.get("kind") == "cloze" or cardgen.is_cloze(front):
        # Text after :: is a hint
        return " ".join(match.group(1).split("::", 1)[0] for match in cardgen._CLOZE_BLOCK_RE.finditer(front))
    return front


def _is_cloze_card(card):
    return card.get("kind") == "cloze" or cardgen.is_cloze(card.get("front"))


def duplicate_indexes(cards):
    """Positions of cards with a word overlap of 0.7 or more with another card."""
    found = set()
    for i, first in enumerate(cards):
        for j in range(i + 1, len(cards)):
            second = cards[j]
            if _is_cloze_card(first) != _is_cloze_card(second):
                continue
            if overlap(_comparable_text(first), _comparable_text(second)) >= DUPLICATE_OVERLAP:
                found.update((i, j))
    return found


def check_cards(cards):
    """The `warn` for each of one highlight's cards, in order: a sentence or None.
    Cards whose state isn't draft are left out of the comparison and get None."""
    live = [i for i, card in enumerate(cards) if card.get("state", "draft") == "draft"]
    duplicates = {live[i] for i in duplicate_indexes([cards[i] for i in live])}
    warnings = [None] * len(cards)
    for i in live:
        warnings[i] = DUPLICATE_WARNING if i in duplicates else None
    return warnings
