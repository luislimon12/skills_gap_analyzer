"""Role 3 tests: curated + O*NET taxonomy, ambiguous-term matching, and the O*NET baseline."""

import pytest

from src.scoring.onet_baseline import occupation_codes_for_role, onet_baseline
from src.scoring.skill_matcher import Skill, SkillMatcher, load_taxonomy, match_skills, merge_taxonomies
from src.scoring.taxonomy.setup_taxonomy import build_onet_taxonomy, clean_onet_example


# ---------- matching ----------

def test_checked_in_taxonomy_loads_without_ambiguous_terms():
    matcher = SkillMatcher()  # raises ValueError on any term owned by two skills
    names = {skill.name for skill in matcher.skills}
    assert len(matcher.skills) > 250
    assert {"Python", "SQL", "Snowflake", "Apache Kafka", "Apache Maven"} <= names
    assert any(skill.source == "onet" for skill in matcher.skills)


@pytest.mark.parametrize(
    "text, present, absent",
    [
        ("You will excel in a fast-paced team.", [], ["Microsoft Excel"]),
        ("Advanced Excel and pivot tables.", ["Microsoft Excel", "Excel Pivot Tables"], []),
        ("Our R&D group works with the C-suite.", [], ["R", "C"]),
        ("Python/R and C/C++ experience.", ["Python", "R", "C", "C++"], []),
        ("C# developer", ["C#"], ["C"]),
        ("We spark innovation at a swift pace.", [], ["Apache Spark", "Swift"]),
        ("Built pipelines in PySpark and Spark.", ["Apache Spark"], []),
        ("reporting to the Director of Finance", [], ["Report Development"]),
        ("Machine learning (ML) and AI.", ["Machine Learning", "Artificial Intelligence"], []),
        ("Build Maven projects deployed on Tomcat", ["Apache Maven", "Apache Tomcat"], []),
    ],
)
def test_matcher_handles_ambiguous_terms(text, present, absent):
    found = match_skills(text)
    for skill in present:
        assert skill in found
    for skill in absent:
        assert skill not in found


def test_merge_keeps_curated_terms_authoritative():
    curated = [Skill("Apache Kafka", aliases=("Kafka",)), Skill("Java")]
    supplement = [
        Skill("Apache Kafka", aliases=("Kafka Streams",), source="onet"),  # same skill: aliases merge
        Skill("Oracle Java", aliases=("Java",), source="onet"),            # alias taken: alias dropped
        Skill("Kafka", source="onet"),                                     # name taken: skill dropped
    ]
    merged = {skill.name: skill for skill in merge_taxonomies(curated, supplement)}
    assert merged["Apache Kafka"].aliases == ("Kafka", "Kafka Streams")
    assert merged["Oracle Java"].aliases == ()
    assert "Kafka" not in merged


def test_explicit_taxonomy_path_and_case_sensitive_column(tmp_path):
    path = tmp_path / "tax.csv"
    path.write_text(
        "skill_name,category,aliases,case_sensitive_terms\n"
        "R,Languages,R programming,R\nGo,Languages,Golang,Go\n",
        encoding="utf-8",
    )
    skills = load_taxonomy(path)
    assert [s.name for s in skills] == ["R", "Go"]
    assert match_skills("go to market with R", path) == ["R"]
    assert match_skills("Go and r programming", path) == ["R", "Go"]


# ---------- O*NET import ----------

@pytest.mark.parametrize(
    "example, expected",
    [
        ("Amazon Web Services AWS software", ("AWS", ["Amazon Web Services AWS software"])),
        ("Structured query language SQL", ("SQL", ["Structured query language", "Structured query language SQL"])),
        ("Apache Kafka", ("Apache Kafka", ["Kafka"])),
        ("Apache Subversion SVN", ("Apache Subversion", ["SVN", "Subversion", "Apache Subversion SVN"])),
        ("eClinicalWorks EHR software", ("eClinicalWorks EHR", ["eClinicalWorks EHR software"])),
        ("Microsoft Project", ("Microsoft Project", [])),       # "Project" alone is too generic
        ("Database reporting software", None),                  # a category, not a product
        ("Facebook", None),
    ],
)
def test_clean_onet_example(example, expected):
    assert clean_onet_example(example) == expected


def test_build_onet_taxonomy_maps_to_curated_and_adds_new(tmp_path):
    curated = tmp_path / "curated.csv"
    curated.write_text("skill_name,category,aliases\nSQL,Databases,\nPython,Languages,\n", encoding="utf-8")
    def row(soc, example, title, hot="N", in_demand="N"):
        return {"O*NET-SOC Code": soc, "Example": example, "Commodity Code": "1",
                "Commodity Title": title, "Hot Technology": hot, "In Demand": in_demand}

    rows = [
        row("15-2051.00", "Structured query language SQL", "Data base user interface and query software", "Y", "Y"),
        row("15-2051.00", "Apache Kafka", "Data base management system software", "Y"),
        row("15-2051.00", "Obscure Tool", "Other software"),             # not hot, 1 occupation
        row("29-1141.00", "Epic Systems", "Medical software", "Y"),
        row("43-3031.00", "Tax software", "Tax preparation software"),  # a category, not a product
    ] + [row(f"43-{i}.00", "Intuit QuickBooks", "Accounting software") for i in range(5)]
    titles = {"15-2051.00": "Data Scientists", "29-1141.00": "Registered Nurses"}
    skills, mapping = build_onet_taxonomy(rows, titles, curated_path=curated)

    # SQL is curated; Kafka/Epic are hot; QuickBooks is used by 5 occupations.
    assert [s["skill_name"] for s in skills] == ["Apache Kafka", "Epic Systems", "Intuit QuickBooks"]
    assert skills[0]["category"] == "Database management system"
    assert skills[2]["aliases"] == "QuickBooks"
    by_skill = {(m["onet_soc_code"], m["skill_name"]): m for m in mapping}
    assert by_skill[("15-2051.00", "SQL")]["in_demand"] == "Y"
    assert by_skill[("15-2051.00", "Obscure Tool")]["in_taxonomy"] == "N"   # baseline only
    assert by_skill[("29-1141.00", "Epic Systems")]["occupation_title"] == "Registered Nurses"
    assert not any(m["skill_name"] == "Tax" for m in mapping)

    nurse_only, _ = build_onet_taxonomy(rows, titles, curated_path=curated, soc_prefixes=("29-",))
    assert [s["skill_name"] for s in nurse_only] == ["Epic Systems"]


def test_build_tool_mapping_and_title_index():
    from src.scoring.taxonomy.setup_taxonomy import build_title_index, build_tool_mapping

    tools = [{"O*NET-SOC Code": "53-7051.00", "Example": "Forklifts"}] + [
        {"O*NET-SOC Code": f"99-{i:04d}.00", "Example": "Personal computers"} for i in range(70)
    ]
    mapping = build_tool_mapping(tools, {"53-7051.00": "Industrial Truck and Tractor Operators"})
    assert [(m["skill_name"], m["kind"]) for m in mapping] == [("Forklifts", "tool")]  # generic tool dropped

    titles = build_title_index(
        [{"O*NET-SOC Code": "31-1122.00", "Title": "Personal Care Aides"}],
        [{"O*NET-SOC Code": "31-1122.00", "Alternate Title": "Personal Support Worker", "Short Title": "PSW"}],
        [{"O*NET-SOC Code": "31-1122.00", "Reported Job Title": "personal support worker"}],  # duplicate
    )
    assert [(t["title"], t["kind"]) for t in titles] == [
        ("Personal Care Aides", "occupation"), ("Personal Support Worker", "alternate"), ("PSW", "alternate"),
    ]


# ---------- O*NET baseline ----------

MAPPING = [
    {"onet_soc_code": "15-2051.00", "occupation_title": "Data Scientists", "skill_name": "Python",
     "hot_technology": "Y", "in_demand": "Y"},
    {"onet_soc_code": "15-2051.00", "occupation_title": "Data Scientists", "skill_name": "SAS",
     "hot_technology": "Y", "in_demand": "N"},
    {"onet_soc_code": "15-2041.00", "occupation_title": "Statisticians", "skill_name": "Python",
     "hot_technology": "Y", "in_demand": "N"},
    {"onet_soc_code": "15-1252.00", "occupation_title": "Software Developers", "skill_name": "Java",
     "hot_technology": "Y", "in_demand": "Y"},
]


def test_onet_baseline_aggregates_selected_occupations():
    baseline = onet_baseline(["15-2051.00", "15-2041.00"], ["python"], MAPPING)
    assert [row["skill"] for row in baseline] == ["Python", "SAS"]
    assert baseline[0]["occupation_share"] == 1.0
    assert baseline[0]["has_skill"] is True and baseline[0]["in_demand"] is True
    assert baseline[1]["occupations"] == ["Data Scientists"]
    assert onet_baseline([], [], MAPPING) == []


def test_occupation_codes_for_role_is_case_insensitive():
    role_map = {"Data Scientist": ["15-2051.00"]}
    assert occupation_codes_for_role(" data scientist ", role_map) == ["15-2051.00"]
    # Unlisted roles fall back to O*NET title matching
    assert "35-1011.00" in occupation_codes_for_role("Chef", role_map)   # Chefs and Head Cooks
    assert occupation_codes_for_role("zzzz qqqq", None) == []


def test_checked_in_mapping_covers_configured_roles():
    from src.dashboard.utils import load_settings

    settings = load_settings()
    for role, codes in settings["onet_occupations"].items():
        assert onet_baseline(codes), f"No O*NET baseline rows for {role}"
    for role in settings["target_roles"]:
        codes = occupation_codes_for_role(role, settings["onet_occupations"])
        assert codes, f"No O*NET occupation matched {role}"
        assert onet_baseline(codes, kind=None), f"No O*NET baseline rows for {role}"


@pytest.mark.parametrize(
    "role, expected_soc",
    [
        ("Registered Nurse", "29-1141.00"),
        ("PSW", "31-1122.00"),
        ("Personal Support Worker", "31-1122.00"),
        ("Electrician", "47-2111.00"),
        ("Forklift Operator", "53-7051.00"),
        ("AZ Driver", "53-3032.00"),
        ("Accountant", "13-2011.00"),
        ("Customer Service Representative", "43-4051.00"),
        ("Early Childhood Educator", "25-2011.00"),
        ("Senior Software Developer", "15-1252.00"),
    ],
)
def test_match_occupations_finds_the_right_occupation_for_many_job_types(role, expected_soc):
    from src.scoring.onet_baseline import match_occupations

    matches = match_occupations(role)
    assert matches and matches[0]["soc"] == expected_soc, matches


def test_baseline_tools_use_plural_tolerant_resume_mentions():
    mapping = [
        {"onet_soc_code": "53-7051.00", "occupation_title": "Industrial Truck and Tractor Operators",
         "kind": "tool", "skill_name": "Forklifts", "hot_technology": "N", "in_demand": "N"},
        {"onet_soc_code": "53-7051.00", "occupation_title": "Industrial Truck and Tractor Operators",
         "kind": "tool", "skill_name": "Pallet jacks", "hot_technology": "N", "in_demand": "N"},
    ]
    tools = onet_baseline(["53-7051.00"], [], mapping, resume_text="Certified forklift operator", kind="tool")
    assert {row["skill"]: row["has_skill"] for row in tools} == {"Forklifts": True, "Pallet jacks": False}
    assert onet_baseline(["53-7051.00"], [], mapping, kind="technology") == []


@pytest.mark.parametrize(
    "text, present, absent",
    [
        ("Valid G licence and clean driver’s abstract required",
         ["Driver's Licence", "Clean Driving Record"], []),
        ("RPN with BLS and IPAC training",
         ["Nursing Registration", "Basic Life Support", "Infection Prevention and Control"], []),
        ("Smart Serve certified, POS and cash handling",
         ["Smart Serve", "Point of Sale Systems", "Cash Handling"], []),
        ("Red Seal electrician, Canadian Electrical Code, WHMIS",
         ["Red Seal", "Canadian Electrical Code", "WHMIS"], []),
        ("We offer competitive compensation, health insurance, flexible scheduling and mental health days",
         [], ["Compensation and Benefits", "Insurance Claims and Policies", "Calendar Management",
              "Mental Health Care", "Adaptability"]),
        ("Experience with MRP and SSO", ["MRP", "SSO"], []),
        ("the mrp of this sso", [], ["MRP", "SSO"]),
    ],
)
def test_general_taxonomy_matches_non_tech_requirements(text, present, absent):
    found = match_skills(text)
    for skill in present:
        assert skill in found, (skill, found)
    for skill in absent:
        assert skill not in found, (skill, found)


def test_mask_removes_taxonomy_terms_without_corrupting_text():
    masked = SkillMatcher().mask("Able to lift up to 50 lbs. Microsoft Excel and SAP experience")
    assert "Excel" not in masked and "SAP" not in masked and "lift up to" not in masked
    assert masked.startswith("Able to") and masked.rstrip().endswith("experience")


def test_discover_phrases_finds_uncatalogued_requirements():
    from src.scoring.phrase_discovery import candidate_phrases, discover_phrases

    assert "long-haul driving" in candidate_phrases("2 years of long-haul driving experience.")
    postings = [
        {"title": "AZ Driver", "full_text": text}
        for text in [
            "Long-haul driving across Canada. Cross-border routes. Reefer trailers. WHMIS.",
            "Cross-border routes and long-haul driving required. Valid G licence.",
            "Long-haul driving, flatbed securement, cross-border routes.",
            "Local routes, flatbed securement and tarping. Competitive pay.",
        ]
    ]
    rows = discover_phrases(postings, resume_text="5 years of long-haul driving", role="AZ Driver", min_count=2)
    phrases = {row["phrase"]: row for row in rows}
    assert {"cross-border routes", "long-haul driving", "flatbed securement"} <= set(phrases)
    assert phrases["long-haul driving"]["in_resume"] is True
    assert phrases["cross-border routes"]["share"] == 0.75
    assert not any("whmis" in p or "licence" in p or "driver" in p for p in phrases)  # taxonomy / role words
