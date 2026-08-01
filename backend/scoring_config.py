"""Adjustable scoring weights and decision thresholds."""

EVIDENCE_WEIGHTS = {
    "breadth": 30,
    "replication": 15,
    "synthesis": 15,
    "real_world": 20,
    "direction": 20,
}

LINK_WEIGHTS = {
    "technology": 0.40,
    "use_case": 0.30,
    "context": 0.20,
    "expected_value": 0.10,
}

LINK_THRESHOLDS = {
    "direct": 0.80,
    "partial": 0.45,
    "candidate": 0.30,
}

LABEL_THRESHOLDS = {
    "min_coverage": 50,
    "min_evidence": 40,
    "gap_evidence": 70,
    "gap_max_adoption": 30,
    "gap_coverage": 70,
    "no_gap_adoption": 60,
    "no_gap_direct_production_orgs": 2,
    "deep_research_priority": 45,
}

ADOPTION_POINTS = {
    "end_user_use": {
        "direct": {"production": 45, "limited_deployment": 25, "pilot": 12, "unknown": 8},
        "partial": {"production": 25, "limited_deployment": 14, "pilot": 7, "unknown": 4},
    },
    "vendor_internal_use": {
        "direct": {"production": 20, "limited_deployment": 12, "pilot": 7, "unknown": 4},
        "partial": {"production": 10, "limited_deployment": 6, "pilot": 3, "unknown": 2},
    },
}

VENDOR_PRODUCT_INTEGRATION_POINTS = 4
ORGANIZATION_MAX = 30
BASE_ADOPTION_MAX = 75
QUERY_FAMILY_MAX = 3
MAX_RESEARCH_CLUSTERS = 5
MAX_ADOPTION_CLUSTERS = 5
MAX_LINK_CANDIDATES_PER_RESEARCH = 5
MAX_CANDIDATE_CONNECTIONS = 2
