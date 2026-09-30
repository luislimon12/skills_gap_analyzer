"""Cosine-similarity fit scoring for resume and role-demand vectors."""

from collections.abc import Mapping, Sequence
from math import sqrt


def fit_score(resume_vector, demand_vector) -> float:
    """Return cosine similarity in [0, 1]; two zero vectors have score zero.

    Vectors may be equally sized numeric sequences or mappings keyed by skill.
    """
    if isinstance(resume_vector, Mapping) and isinstance(demand_vector, Mapping):
        keys = resume_vector.keys() | demand_vector.keys()
        resume_values = [float(resume_vector.get(key, 0.0)) for key in keys]
        demand_values = [float(demand_vector.get(key, 0.0)) for key in keys]
    elif isinstance(resume_vector, Sequence) and isinstance(demand_vector, Sequence):
        if len(resume_vector) != len(demand_vector):
            raise ValueError("Resume and demand vectors must have the same length.")
        resume_values = [float(value) for value in resume_vector]
        demand_values = [float(value) for value in demand_vector]
    else:
        raise TypeError("Vectors must both be mappings or equally sized sequences.")

    dot_product = sum(left * right for left, right in zip(resume_values, demand_values))
    resume_norm = sqrt(sum(value * value for value in resume_values))
    demand_norm = sqrt(sum(value * value for value in demand_values))
    if resume_norm == 0.0 or demand_norm == 0.0:
        return 0.0
    return dot_product / (resume_norm * demand_norm)
