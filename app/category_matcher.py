import re
import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
    logger.addHandler(console)

class CategoryMatcher:
    """
    A CategoryMatcher that:
      - Loads categories once
      - Uses word-level overlap for matching
      - Falls back to 'Shopping' (expense) or 'Other Income' (income) if below threshold
    """

    def __init__(self, db_cursor):
        logger.info("Initializing CategoryMatcher ...")
        self.cursor = db_cursor
        self.categories = self._load_categories()
        self.category_keywords = self._initialize_keywords()

        logger.debug("Categories loaded (ID -> Name, Type):")
        for c_id, cat in self.categories.items():
            logger.debug(f"  {c_id} -> {cat['name']} ({cat['type']})")

        logger.info(f"CategoryMatcher ready with {len(self.categories)} categories.\n")

    def _load_categories(self) -> Dict[int, Dict]:
        """
        Load categories from the DB. Expects columns: id, name, type, description.
        Returns {cat_id: {"id", "name", "type", "description"}}
        """
        query = """SELECT id, name, type, description FROM categories ORDER BY id"""
        self.cursor.execute(query)
        rows = self.cursor.fetchall()

        categories = {}
        # Attempt to handle dictionary or tuple rows
        if rows and isinstance(rows[0], dict):
            for row in rows:
                c_id = row["id"]
                categories[c_id] = {
                    "id": c_id,
                    "name": row["name"],
                    "type": row["type"],
                    "description": row["description"]
                }
        else:
            for row in rows:
                c_id, name, ctype, desc = row
                categories[c_id] = {
                    "id": c_id,
                    "name": name,
                    "type": ctype,
                    "description": desc
                }

        return categories

    def _initialize_keywords(self) -> Dict[int, List[str]]:
        """
        Build a list of keywords for each category (including synonyms).
        We'll do word-level matching to avoid partial substrings.
        """

        # --- UPDATED EMI & Payments synonyms to include "credit", "repaid", "friend" ---
        keyword_patterns = {
            "Salary": ["salary", "wages", "pay", "payroll", "employment"],
            "Investment Returns": ["investment return", "mutual fund", "mf", "returns", "dividend", "interest"],
            "Freelance": ["freelance", "contract", "consulting", "project", "gig"],
            "Other Income": ["other", "miscellaneous", "misc"],
            "Deposit": ["deposit", "cash deposit", "bank deposit"],
            "Food & Dining": [
                "grocery", "groceries", "food", "dining", "restaurant",
                "swiggy", "zomato", "supermarket", "mart", "bazaar"
            ],
            "Utilities": [
                "electricity", "electric", "power", "utility", "utilities",
                "internet", "broadband", "water", "gas", "bill payment"
            ],
            "Transportation": [
                "transport", "fuel", "petrol", "diesel", "gas", "metro",
                "bus", "taxi", "uber", "ola", "travel", "transportation"
            ],
            "Health": [
                "health", "medical", "doctor", "hospital", "pharmacy",
                "medicine", "healthcare", "clinic", "prescription", "fitness", "gym"
            ],
            "Shopping": [
                "shopping", "purchase", "store", "retail", "amazon",
                "flipkart", "mall", "market", "buy", "online", "shop"
            ],
            "EMI & Payments": [
                "emi", "loan", "payment", "credit", "card", "mortgage",
                "debt", "installment", "finance", "insurance", "repaid", "friend"
            ],
            "Investment": [
                "investment", "invest", "mutual fund", "mf", "stocks",
                "shares", "securities", "sip", "portfolio", "redeemed"
            ],
            "Entertainment": [
                "entertainment", "movie", "theatre", "recreation",
                "game", "sports", "leisure", "fun"
            ],
            "Internal Transfer": ["transfer", "mov", "internal", "account transfer"]
        }

        cat_keywords = {}
        for c_id, info in self.categories.items():
            name = info["name"].strip().lower()
            desc = (info["description"] or "").strip().lower()

            # Start with words from the name + description
            words = self._clean_text(name).split()
            if desc:
                words += self._clean_text(desc).split()

            # Add known patterns if the name matches
            for pattern_name, synonyms in keyword_patterns.items():
                if pattern_name.lower() in name:
                    # e.g. "EMI & Payments" in "emi & payments"
                    for phrase in synonyms:
                        words += self._clean_text(phrase).split()

            # De-duplicate
            cat_keywords[c_id] = list(set(words))

        return cat_keywords

    def _clean_text(self, text: str) -> str:
        """
        Lowercase, remove punctuation, compress whitespace.
        """
        text = text.lower()
        text = re.sub(r"[^\w\s]+", " ", text)
        return " ".join(text.split())

    def _calculate_match_score(self, text: str, category_id: int) -> float:
        """
        Word-level overlap ratio.
        """
        text_words = set(self._clean_text(text).split())
        cat_words = set(self.category_keywords.get(category_id, []))
        if not cat_words:
            return 0.0

        overlap = text_words & cat_words
        if not overlap:
            return 0.0

        # Overlap ratio = (# of common words) / max(len(text_words), len(cat_words))
        return len(overlap) / max(len(text_words), len(cat_words))

    def match_category(self, description: str, account: str, trans_type: str, threshold: float = 0.2) -> Tuple[int, float]:
        """
        Finds the best matching category for the given transaction.
        Falls back to "Shopping" (expense) or "Other Income" (income) if best < threshold.
        """
        logger.info(f"Matching category for desc='{description}', acct='{account}', type='{trans_type}'")

        combined_text = f"{description} {account}"
        # Filter categories by transaction type
        relevant = {cid: c for cid, c in self.categories.items() if c["type"] == trans_type}
        logger.debug(f"Relevant: {[c['name'] for c in relevant.values()]}")

        best_id = None
        best_score = 0.0
        for cid in relevant:
            score = self._calculate_match_score(combined_text, cid)
            logger.debug(f"  -> Score {score:.3f} for ID={cid} ({self.categories[cid]['name']})")
            if score > best_score:
                best_score = score
                best_id = cid

        # If no match or below threshold, fallback
        if best_id is None or best_score < threshold:
            logger.debug(f"No match >= threshold={threshold}; fallback logic triggered.")
            fallback = None
            if trans_type.lower() == "expense":
                # Try to find "Shopping" for fallback
                fallback = next(
                    (cid for cid, cat in self.categories.items()
                     if cat["type"] == "expense" and cat["name"].lower() == "shopping"),
                    None
                )
            else:
                # For income, fallback to "Other Income"
                fallback = next(
                    (cid for cid, cat in self.categories.items()
                     if cat["type"] == "income" and cat["name"].lower() == "other income"),
                    None
                )

            if not fallback and relevant:
                # If we still don't have a fallback, pick the first relevant
                fallback = next(iter(relevant))

            best_id = fallback
            best_score = 0.0

        final_name = self.categories[best_id]["name"] if best_id else "Unknown"
        logger.info(f"Final best match: ID={best_id} ({final_name}), score={best_score:.2f}\n")
        return best_id, best_score
