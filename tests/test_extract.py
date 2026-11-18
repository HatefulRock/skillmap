import pytest
import yaml

from skillmap.extract import location, roles, seniority
from skillmap.extract.skills import DEFAULT_TAXONOMY, Taxonomy
from skillmap.runner import CONFIG

TAX = Taxonomy.load()


def skills(text, title=""):
    return TAX.extract(title, text)[0]


@pytest.mark.parametrize("text,expected", [
    ("Strong Python and FastAPI skills", {"Python", "FastAPI"}),
    ("Experience with k8s and Docker", {"Kubernetes", "Docker / containers"}),
    ("Services written in Go, Rust and TypeScript", {"Go", "Rust / Scala", "TypeScript"}),
    ("Build RAG pipelines on pgvector", {"RAG", "Vector databases"}),
    ("You know C++ and C#", {"C++ / C#"}),
    ("Ship agentic workflows using the Model Context Protocol", {"AI agents / agentic workflows", "MCP"}),
])
def test_finds_skills(text, expected):
    assert expected <= set(skills(text))


@pytest.mark.parametrize("text,absent", [
    ("We go to great lengths to support you", "Go"),
    ("Let's go beyond the obvious", "Go"),
    ("JavaScript and TypeScript", "Java / Kotlin (Spring)"),
    ("We rely on rigorous research", "Rust / Scala"),
    ("A fast-paced environment with a ragged edge", "RAG"),
    ("Your travel agent will book flights", "AI agents / agentic workflows"),
    ("mcp is lowercase here", "MCP"),
])
def test_avoids_false_positives(text, absent):
    assert absent not in skills(text)


def test_nice_to_have_section():
    found, nice = TAX.extract("Engineer", "<h3>Requirements</h3><ul><li>Python</li></ul><h3>Nice to have</h3><ul><li>Rust</li></ul>")
    assert "Python" in found and "Rust / Scala" in found
    assert nice == ["Rust / Scala"]


def test_inline_plus_is_nice():
    _, nice = TAX.extract("Engineer", "Python required.\nExperience with Kafka is a plus.")
    assert nice == ["Kafka / message queues / streaming"]


def test_title_counts():
    assert "Python" in skills("Build things.", title="Senior Python Developer")


def test_taxonomy_matches_census_names():
    names = {s.name for s in TAX.skills}
    assert len(names) == len(TAX.skills)
    assert all(s.patterns for s in TAX.skills)
    assert DEFAULT_TAXONOMY.exists()


def test_my_skills_use_taxonomy_names():
    ratings = yaml.safe_load((CONFIG / "my_skills.yaml").read_text())["ratings"]
    assert set(ratings) <= {s.name for s in TAX.skills}
    assert all(1 <= v <= 5 for v in ratings.values())


@pytest.mark.parametrize("title,text,hint,expected", [
    ("Senior ML Engineer", "", None, "senior"),
    ("Staff Software Engineer", "", None, "staff"),
    ("Machine Learning Intern", "", None, "intern"),
    ("Head of AI", "", None, "manager"),
    ("Engineer II", "", None, "mid"),
    ("AI Engineer", "We need 5+ years of experience in Python", None, "senior"),
    ("AI Engineer", "At least 2 years of relevant experience", None, "mid"),
    ("AI Engineer", "", "junior", "junior"),
    ("AI Engineer", "", None, "unspecified"),
])
def test_seniority(title, text, hint, expected):
    assert seniority.classify(title, text, hint) == expected


@pytest.mark.parametrize("title,expected", [
    ("Forward Deployed Engineer", "fde"),
    ("Solutions Architect, GenAI", "fde"),
    ("Senior AI Engineer", "ai_ml"),
    ("Machine Learning Engineer", "ai_ml"),
    ("Data Engineer", "data"),
    ("Python Developer", "backend"),
    ("Backend Engineer (Go)", "backend"),
    ("Site Reliability Engineer", "infra"),
    ("iOS Engineer", "frontend"),
    ("Quantitative Developer", "quant"),
    ("Accounting Manager", None),
    ("Travel Agent", None),
    ("Email Marketing Manager", None),
])
def test_roles(title, expected):
    assert roles.classify(title) == expected


@pytest.mark.parametrize("text,hint,expected", [
    ("Singapore", None, ("SG", "Singapore", "Southeast Asia", False)),
    ("San Francisco, CA | Seattle, WA", None, ("US", "San Francisco", "North America", False)),
    ("Remote - Europe", None, (None, None, None, True)),
    ("London, UK", None, ("GB", "London", "Europe", False)),
    ("Anywhere", "SG", ("SG", "Singapore", "Southeast Asia", True)),
])
def test_location(text, hint, expected):
    assert location.normalise(text, hint) == expected


def test_benefits_and_generic_monitoring_are_not_skills():
    tax = Taxonomy.load()

    def found(text):
        required, nice = tax.extract("Engineer", text)
        return set(required) | set(nice)

    assert not found("Benefits: health insurance, dental and vision insurance, 401k.")
    assert "Financial services / banking domain" in found("We build software for the insurance industry.")
    assert not found("Monitoring of patient outcomes.")
    assert "Observability / tracing / monitoring" in found("Experience with monitoring and alerting.")
    assert "Observability / tracing / monitoring" not in found("Own transaction monitoring for AML.")
