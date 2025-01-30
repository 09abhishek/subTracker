import re
import logging
from typing import List, Dict, Tuple

# Example: from mysql.connector.cursor import MySQLCursor

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s:%(name)s: %(message)s"))
    logger.addHandler(console)


class CategoryMatcher:
    """
    A cleaner CategoryMatcher that:
      - Loads categories once
      - Uses word-level overlap instead of substring matching
      - Logs each step clearly
      - Falls back to 'Shopping' (expense) or 'Other Income' (income) if no good match
    """

    def __init__(self, db_cursor):
        """
        :param db_cursor: A live DB cursor to access the 'categories' table
        """
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
        Load all categories from DB.  Expects columns: id, name, type, description
        Returns a dict: { category_id: { "id", "name", "type", "description" }, ... }
        """
        query = """SELECT id, name, type, description FROM categories ORDER BY id"""
        self.cursor.execute(query)
        rows = self.cursor.fetchall()

        categories = {}
        if rows and isinstance(rows[0], dict):
            # Some DB drivers return dict-like rows
            for row in rows:
                c_id = row["id"]
                categories[c_id] = {
                    "id": c_id,
                    "name": row["name"],
                    "type": row["type"],
                    "description": row["description"],
                }
        else:
            # Tuple-based rows (id, name, type, desc)
            for row in rows:
                c_id, name, ctype, desc = row
                categories[c_id] = {
                    "id": c_id,
                    "name": name,
                    "type": ctype,
                    "description": desc,
                }

        return categories

    def _initialize_keywords(self) -> Dict[int, List[str]]:
        """
        Build a list of keywords for each category (including synonyms).
        We'll do word-level matching, so we just store lists of words.
        """
        # Predefined synonyms based on category name patterns
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
                "emi", "loan", "payment", "credit card", "mortgage",
                "debt", "installment", "finance", "insurance"
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

            # Start with name-based words
            # e.g. "Food & Dining" -> ["food", "&", "dining"] if we do simple .split()
            # But we might want to remove punctuation:
            words = self._clean_text(name).split()

            # Add description words, if any
            if desc:
                words += self._clean_text(desc).split()

            # If there's a pattern that matches the category name exactly, add them
            for pattern_name, synonyms in keyword_patterns.items():
                if pattern_name.lower() in name:
                    # e.g. name="Food & Dining" matches "Food & Dining"
                    # so we add ["food", "dining", "restaurant", ...]
                    extra_words = []
                    for s in synonyms:
                        # We'll also clean the synonyms so we consistently compare
                        extra_words += self._clean_text(s).split()
                    words += extra_words

            # Remove duplicates and store
            cat_keywords[c_id] = list(set(words))

        return cat_keywords

    def _clean_text(self, text: str) -> str:
        """
        Lowercase and remove punctuation for simpler word splits.
        This ensures 'expenses' won't match 'expense' by substring.
        """
        text = text.lower()
        # Replace punctuation with space
        text = re.sub(r"[^\w\s]+", " ", text)
        # Collapse multiple spaces
        return " ".join(text.split())

    def _calculate_match_score(self, text: str, category_id: int) -> float:
        """
        Word-level overlap:
        1) Split text into words
        2) Compare to the category's known keywords
        3) Score = number_of_common_words / max( len(category_words), len(text_words) )
        """
        text_words = set(self._clean_text(text).split())
        cat_words = set(self.category_keywords.get(category_id, []))

        if not cat_words:
            return 0.0

        common = text_words & cat_words
        if not common:
            return 0.0

        # Simple overlap ratio
        overlap_score = len(common) / max(len(cat_words), len(text_words))
        return overlap_score

    def match_category(self, description: str, account: str, trans_type: str, threshold: float = 0.2) -> Tuple[int, float]:
        """
        Determine the best matching category among those with the same type (income/expense).
        If best score < threshold, fallback to 'Shopping' (if expense) or 'Other Income' (if income).
        """
        logger.info(f"Matching category for desc='{description}', acct='{account}', type='{trans_type}'")

        # Combine description + account for more context
        text_to_match = f"{description} {account}"

        # Filter categories by matching 'type'
        relevant = {cid: c for cid, c in self.categories.items() if c["type"] == trans_type}
        logger.debug(f"Relevant categories: {[c['name'] for c in relevant.values()]}")

        best_id = None
        best_score = 0.0

        for cid in relevant:
            score = self._calculate_match_score(text_to_match, cid)
            logger.debug(f"    -> Score for ID={cid} ({self.categories[cid]['name']}): {score:.3f}")
            if score > best_score:
                best_score = score
                best_id = cid

        # Fallback logic if below threshold
        if best_id is None or best_score < threshold:
            logger.debug(f"No match above threshold={threshold}; applying fallback.")
            fallback = None
            if trans_type.lower() == "expense":
                # Try to find the "Shopping" category
                fallback = next(
                    (cid for cid, cat in self.categories.items()
                     if cat["type"] == "expense" and cat["name"].lower() == "shopping"),
                    None
                )
            else:
                # For "income", fallback to "Other Income"
                fallback = next(
                    (cid for cid, cat in self.categories.items()
                     if cat["type"] == "income" and cat["name"].lower() == "other income"),
                    None
                )

            # If no such category, pick the first relevant
            if not fallback and relevant:
                fallback = next(iter(relevant))

            best_id = fallback
            best_score = 0.0

        final_name = self.categories[best_id]["name"] if best_id else "Unknown"
        logger.info(f"Final best match: ID={best_id} ({final_name}), score={best_score:.2f}\n")
        return best_id, best_score
