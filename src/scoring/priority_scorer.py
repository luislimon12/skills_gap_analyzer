"""Gap scoring: weighted posting demand multiplied by resume-skill absence."""


def priority_score(weighted_demand: float, has_skill: bool | int) -> float:
    """Return GapScore = weighted_demand * (1 - has_skill)."""
    if not 0.0 <= weighted_demand <= 1.0:
        raise ValueError("weighted_demand must be between 0 and 1.")
    if has_skill not in (False, True, 0, 1):
        raise ValueError("has_skill must be a boolean or 0/1.")
    return weighted_demand * (1 - int(has_skill))
