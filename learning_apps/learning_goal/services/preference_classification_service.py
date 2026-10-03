"""Classify a learner-authored goal into the product taxonomy."""

import json
import logging
import re
from typing import Any, Dict, Optional

from learning_apps.infrastructure.services.llm_gateway import llm_gateway


# --- Preference classification helpers ---

_PREFERENCE_KEYWORDS: Dict[str, Dict[str, list[str]]] = {
    "mathematics": {
        "algebra": [
            "algebra",
            "equation",
            "inequality",
            "linear",
            "quadratic",
            "polynomial",
            "function",
            "factor",
            "simplify",
            "expression",
            "variable",
            "slope",
            "intercept",
        ],
        "geometry": [
            "geometry",
            "triangle",
            "circle",
            "angle",
            "coordinate",
            "polygon",
            "perimeter",
            "area",
            "volume",
            "theorem",
            "congruent",
            "similar",
            "diagram",
        ],
        "arithmetic": [
            "arithmetic",
            "fraction",
            "percent",
            "ratio",
            "proportion",
            "decimal",
            "integer",
            "addition",
            "subtraction",
            "multiplication",
            "division",
        ],
        "trigonometry": [
            "trigonometry",
            "sine",
            "cosine",
            "tangent",
            "unit circle",
            "radian",
            "triangle trig",
        ],
        "precalculus": [
            "precalculus",
            "functions",
            "graphs",
            "exponents",
            "logarithm",
            "log",
        ],
        "calculus": [
            "calculus",
            "derivative",
            "integral",
            "limit",
            "series",
            "taylor",
            "optimization",
            "differential",
        ],
        "statistics": [
            "statistics",
            "probability",
            "distribution",
            "mean",
            "median",
            "variance",
            "standard deviation",
            "hypothesis test",
            "regression",
            "correlation",
        ],
        "linear_algebra": [
            "linear algebra",
            "matrix",
            "vector",
            "eigenvalue",
            "eigenvector",
            "determinant",
            "rank",
            "nullspace",
            "basis",
            "span",
        ],
        "discrete_math": [
            "discrete math",
            "combinatorics",
            "graph theory",
            "logic",
            "set theory",
            "recursion",
            "induction",
        ],
        "number_theory": [
            "number theory",
            "prime",
            "gcd",
            "lcm",
            "mod",
            "modular arithmetic",
            "congruence",
            "diophantine",
        ],
        "differential_equations": [
            "differential equation",
            "ode",
            "pde",
            "initial condition",
            "boundary condition",
            "laplace transform",
        ],
        "optimization": [
            "optimization",
            "linear programming",
            "gradient descent",
            "convex",
            "objective function",
            "constraints",
        ],
        "real_analysis": [
            "real analysis",
            "epsilon",
            "delta",
            "sequence",
            "series",
            "continuity",
            "convergence",
        ],
    },
    "science": {
        "physics": ["physics", "force", "motion", "energy", "momentum", "waves", "optics"],
        "classical_mechanics": ["mechanics", "kinematics", "dynamics", "newton", "friction", "projectile"],
        "electromagnetism": ["electromagnetism", "electric field", "magnetic field", "charge", "current", "maxwell"],
        "quantum_physics": ["quantum", "quantum mechanics", "wavefunction", "uncertainty principle", "schrodinger"],
        "chemistry": ["chemistry", "reaction", "molecule", "atom", "compound", "stoichiometry", "equilibrium"],
        "organic_chemistry": ["organic chemistry", "hydrocarbon", "functional group", "reaction mechanism", "stereochemistry"],
        "physical_chemistry": ["physical chemistry", "thermodynamics", "kinetics", "quantum chemistry", "equilibrium"],
        "biology": [
            "biology",
            "cell",
            "organism",
            "evolution",
            "dna",
            "metabolism",
            "cellular respiration",
            "respiration",
            "glycolysis",
            "atp",
            "mitochondria",
            "oxidative phosphorylation",
        ],
        "genetics": ["genetics", "dna", "rna", "inheritance", "mutation"],
        "earth_science": ["geology", "earth", "climate", "weather", "planet"],
        "astronomy": ["astronomy", "space", "telescope", "planet", "star", "galaxy", "cosmology"],
        "thermodynamics": ["thermodynamics", "entropy", "heat", "temperature", "engine", "carnot"],
    },
    "engineering": {
        "mechanical": ["mechanical", "machine", "dynamics", "statics", "materials", "cad"],
        "electrical": ["electrical", "circuit", "voltage", "current", "signal", "electronics", "power"],
        "civil": ["civil engineering", "structures", "structural", "concrete", "bridge", "geotechnical"],
        "chemical": ["chemical engineering", "process", "reaction engineering", "distillation", "fluid", "mass transfer"],
        "computer": ["computer engineering", "embedded", "hardware", "fpga", "microcontroller", "digital logic"],
        "software": ["software engineering", "system design", "architecture", "testing", "debugging", "ci/cd"],
        "aerospace": ["aerospace", "aerodynamics", "flight", "propulsion", "rocket"],
        "control_systems": ["control systems", "feedback", "pid", "stability", "state space"],
    },
    "computer_science": {
        "programming_fundamentals": [
            "programming",
            "coding",
            "debug",
            "algorithm",
            "variable",
            "loop",
            "function",
            "object oriented",
            "oop",
        ],
        "python": ["python", "pandas", "numpy", "jupyter", "python scripting"],
        "javascript": ["javascript", "js", "node", "react", "typescript", "frontend", "backend"],
        "java": ["java", "jvm", "spring", "object oriented"],
        "c_cpp": ["c", "c++", "cpp", "pointer", "memory management"],
        "web_development": ["web development", "api", "rest", "http", "flask", "django", "fastapi", "html", "css"],
        "data_structures_algorithms": [
            "data structures",
            "data structure",
            "algorithm",
            "leetcode",
            "big o",
            "complexity",
            "array",
            "linked list",
            "tree",
            "graph",
        ],
        "databases": ["database", "sql", "mysql", "postgres", "sqlite", "index", "query optimization"],
        "computer_networks": ["networking", "tcp", "udp", "http", "dns", "routing", "latency"],
        "operating_systems": ["operating systems", "process", "thread", "scheduling", "virtual memory", "filesystem"],
        "distributed_systems": ["distributed systems", "consensus", "raft", "paxos", "replication", "sharding"],
        "cloud_computing": ["cloud", "aws", "gcp", "azure", "kubernetes", "docker", "serverless"],
        "cybersecurity": ["security", "cybersecurity", "encryption", "authentication", "authorization", "owasp", "vulnerability"],
        "machine_learning": ["machine learning", "ml", "model training", "supervised", "unsupervised", "neural network"],
        "deep_learning": ["deep learning", "cnn", "rnn", "transformer", "backpropagation"],
        "nlp": ["nlp", "natural language processing", "tokenization", "embedding", "llm", "prompting"],
    },
    "finance": {
        "budgeting": ["budget", "expense", "savings", "cost"],
        "personal_finance": ["personal finance", "debt", "loan", "credit", "mortgage", "interest rate"],
        "investing": ["investment", "portfolio", "stocks", "bonds", "returns", "risk", "diversification"],
        "accounting": ["accounting", "balance sheet", "income statement", "cash flow statement", "gaap", "depreciation"],
        "corporate_finance": ["corporate finance", "valuation", "discounted cash flow", "dcf", "wacc", "capital structure"],
    },
    "humanities": {
        "history": ["history", "historical", "ancient", "renaissance"],
        "philosophy": ["philosophy", "ethics", "logic"],
        "literature": ["literature", "novel", "poetry"],
        "art_history": ["art history", "art movement", "painting", "sculpture", "renaissance art", "impressionism"],
        "linguistics": ["linguistics", "phonetics", "syntax", "semantics", "morphology", "pragmatics"],
    },
    "wellness": {
        "mental_health": ["mental health", "stress", "anxiety", "mindfulness"],
        "physical_health": ["exercise", "fitness", "workout", "strength training", "cardio"],
        "nutrition": ["nutrition", "diet", "meal plan", "macros", "calories", "protein"],
        "sleep": ["sleep", "insomnia", "circadian", "sleep hygiene"],
    },
    "general-learning": {
        "career_planning": ["career", "job", "interview", "resume", "linkedin"],
        "goal_setting": ["goal", "plan", "roadmap", "milestone", "habit"],
        "study_skills": ["study skills", "productivity", "focus", "note taking", "spaced repetition"],
        "test_prep": ["exam", "test prep", "sat", "gre", "gmat", "toefl", "ielts", "mcat", "lsat"],
    },
    "data_science": {
        "data_analysis": ["data analysis", "analytics", "pandas", "data cleaning", "eda"],
        "data_visualization": ["data visualization", "matplotlib", "seaborn", "plotly", "dashboard", "tableau", "power bi"],
        "sql": ["sql", "join", "group by", "window function", "query"],
        "statistics": ["statistics", "probability", "regression", "hypothesis testing"],
        "experiment_design": ["a/b test", "ab test", "causal", "experiment design", "randomized experiment"],
        "data_engineering": ["data engineering", "etl", "pipeline", "airflow", "spark", "warehouse"],
    },
    "language_learning": {
        "english": ["english", "toefl", "ielts", "esl"],
        "chinese": ["chinese", "mandarin"],
        "spanish": ["spanish", "castilian"],
        "french": ["french"],
        "german": ["german"],
        "japanese": ["japanese", "nihongo"],
        "korean": ["korean", "hangul"],
        "grammar": ["grammar"],
        "pronunciation": ["pronunciation", "accent"],
        "writing": ["writing", "essay"],
        "speaking": ["speaking", "conversation practice"],
        "listening": ["listening", "audio comprehension"],
        "reading": ["reading", "reading comprehension"],
    },
    "business": {
        "entrepreneurship": ["startup", "entrepreneur", "founder", "venture", "fundraising"],
        "marketing": ["marketing", "growth", "seo", "ads", "brand", "positioning"],
        "product_management": ["product management", "pm", "product", "roadmap", "requirements", "user stories"],
        "strategy": ["business strategy", "competitive analysis", "swot", "porter", "moat"],
        "operations": ["operations", "supply chain", "logistics", "process improvement"],
        "leadership": ["leadership", "management", "team", "communication"],
    },
    "social_science": {
        "psychology": ["psychology", "cognitive", "behavior", "learning", "motivation"],
        "economics": ["economics", "microeconomics", "macroeconomics", "supply", "demand", "inflation"],
        "sociology": ["sociology", "society", "social", "culture"],
        "political_science": ["political science", "politics", "government", "public policy", "elections"],
    },
    "medicine": {
        "anatomy": ["anatomy", "organs", "human body", "muscle", "bone"],
        "physiology": ["physiology", "homeostasis", "cardiovascular", "respiratory", "nervous system"],
        "pharmacology": ["pharmacology", "drug", "dose", "side effects", "mechanism of action"],
        "public_health": ["public health", "epidemiology", "prevention", "health policy", "population health"],
    },
}


def _safe_parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extract and parse the first JSON object from a model response."""
    if not text:
        return None
    s = text.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _keyword_matches(text: str, keyword: str) -> bool:
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){escaped}(?!\w)", text) is not None


def _fallback_preference_classification(preference_text: str) -> Dict[str, Any]:
    """Internal helper to handle fallback preference classification."""
    lowered = (preference_text or "").lower()
    for domain, topics in _PREFERENCE_KEYWORDS.items():
        for branch, keywords in topics.items():
            if any(_keyword_matches(lowered, keyword) for keyword in keywords):
                return {
                    "domain": domain,
                    "branch": branch,
                    "confidence": 0.5,
                    "reasoning": f"Keyword match for '{branch}' in {domain}.",
                }
    return {
        "domain": "general-learning",
        "branch": "exploratory",
        "confidence": 0.25,
        "reasoning": "Default fallback classification.",
    }


PREFERENCE_CLASSIFIER_PROMPT = """
You are an educational domain classifier for student learning preferences.

Task:
- Read the learner's free-form preference text.
- Choose the most appropriate high-level domain and branch from the list below.
- If multiple domains/branches are possible, choose the best single pair.
- Provide a confidence score between 0.0 and 1.0.
- Provide a short natural-language reasoning.

Return strict JSON with keys: "domain" (lowercase snake_case), "branch" (lowercase snake_case), "confidence" (float), "reasoning" (string).

Available domain -> branch anchors (not exhaustive, expand when confident):
- mathematics: [arithmetic, algebra, geometry, trigonometry, precalculus, calculus, statistics, linear_algebra, discrete_math, number_theory, differential_equations, optimization, real_analysis, general_math]
- science: [physics, classical_mechanics, electromagnetism, quantum_physics, thermodynamics, chemistry, organic_chemistry, physical_chemistry, biology, genetics, earth_science, astronomy]
- engineering: [electrical, mechanical, civil, chemical, computer, software, aerospace, control_systems]
- computer_science: [programming_fundamentals, python, javascript, java, c_cpp, web_development, data_structures_algorithms, databases, computer_networks, operating_systems, distributed_systems, cloud_computing, cybersecurity, machine_learning, deep_learning, nlp]
- data_science: [data_analysis, data_visualization, sql, statistics, experiment_design, data_engineering]
- finance: [personal_finance, investing, budgeting, accounting, corporate_finance]
- business: [entrepreneurship, marketing, product_management, strategy, operations, leadership]
- language_learning: [english, chinese, spanish, french, german, japanese, korean, grammar, pronunciation, writing, speaking, listening, reading]
- humanities: [philosophy, literature, history, art_history, linguistics]
- social_science: [psychology, economics, sociology, political_science]
- wellness: [mental_health, physical_health, nutrition, mindfulness, sleep]
- medicine: [anatomy, physiology, pharmacology, public_health]
- general-learning: [study_skills, career_planning, goal_setting, test_prep, exploratory]

If the text references future schooling, budgeting, or planning, still choose the closest domain + branch—even if it is finance or general-learning.
If unsure, set domain="general-learning" and branch="exploratory".

Learner preference:
{preference_text}
"""


def _normalize_taxonomy_value(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    # Keep taxonomy values predictable and DB-safe for fixed-length columns.
    normalized = raw.replace("/", "_").replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return (normalized or fallback)[:100]


def classify_preference_only(preference_text: str) -> Dict[str, Any]:
    """
    Classify a natural language preference into (domain, branch) only.
    No knowledge-base generation is performed here.
    """
    try:
        print("[LearningGoalTaxonomy] classification start", flush=True)
    except Exception:
        pass
    base_result = _fallback_preference_classification(preference_text or "")
    result = base_result.copy()

    if preference_text:
        try:
            prompt = PREFERENCE_CLASSIFIER_PROMPT.format(preference_text=preference_text)
            response = llm_gateway.chat_completion_or_raise(
                route="learning_goal.assets",
                model="gpt-5.4-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_completion_tokens=600,
            )
            raw = response.choices[0].message.content or ""
            parsed = _safe_parse_json_object(raw)
            if parsed and parsed.get("domain"):
                result = {
                    "domain": _normalize_taxonomy_value(parsed.get("domain"), base_result["domain"]),
                    "branch": _normalize_taxonomy_value(parsed.get("branch"), base_result["branch"]),
                    "confidence": float(parsed.get("confidence", base_result["confidence"])),
                    "reasoning": parsed.get("reasoning", base_result["reasoning"]),
                }
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Preference LLM classification failed: %s", exc)

    result["domain"] = _normalize_taxonomy_value(result.get("domain"), base_result["domain"])
    result["branch"] = _normalize_taxonomy_value(result.get("branch"), base_result["branch"])

    try:
        print(
            "[LearningGoalTaxonomy] classification result: "
            + json.dumps(result, ensure_ascii=False),
            flush=True,
        )
    except Exception:
        pass

    return result



__all__ = ["classify_preference_only"]
