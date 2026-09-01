import re

LEGAL_SUFFIXES = {
    "limited",
    "ltd",
    "private",
    "pvt",
    "inc",
    "llp",
    "plc",
    "corp",
    "corporation",
    "company",
    "co",
}


def normalize_company_name(name: str) -> str:
    cleaned = name.strip().lower()
    cleaned = cleaned.replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    tokens = [token for token in cleaned.split() if token not in LEGAL_SUFFIXES]
    return " ".join(tokens)
