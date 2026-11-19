from skillmap import vocab
from skillmap.extract.skills import Taxonomy


def test_asked_items_split_lists_after_cues():
    text = "You have experience with Kafka, Flink and dbt. Familiarity with tools such as Temporal or Inngest is a plus."
    assert vocab.asked_items(text) == ["Kafka", "Flink", "dbt", "Temporal", "Inngest"]


def test_measure_counts_gaps_by_company_and_coverage():
    tax = Taxonomy.load()
    ref = [t for t in vocab.load_reference() if t.name in {"Python", "Salesforce"}]
    texts = {f"{c}-{i}": (c, "Experience with Python and Salesforce required.")
             for c in ("A", "B", "C") for i in range(2)}
    report = vocab.measure(texts, tax, ref)
    assert report["ads"] == 6 and report["companies"] == 3
    gaps = {g["term"]: g for g in report["reference"]["gaps"]}
    if not tax.known("Salesforce"):
        assert gaps["Salesforce"]["companies"] == 3 and gaps["Salesforce"]["ads"] == 6
        assert report["reference"]["coverage"] == 0.5
    assert "Python" not in gaps
    assert all(g["item"].lower() != "python" for g in report["asked"]["gaps"])


def test_reference_terms_match_case_sensitively_with_word_edges():
    terms = {t.name: t.pattern for t in vocab.load_reference()}
    assert terms["Go"].search("Go and Rust") and not terms["Go"].search("go to market")
    assert terms["C++"].search("modern C++ code") and terms["Next.js"].search("Next.js apps")
    assert not terms["Java"].search("JavaScript")


def test_covered_counts_a_term_a_skill_matches_in_context():
    tax = Taxonomy.load()
    text = "We write services in Go and Rust. Let's go to the office."
    i = text.index("Go")
    assert vocab.covered(text, i, i + 2, tax)
    j = text.index("Rust")
    assert vocab.covered(text, j, j + 4, tax)
