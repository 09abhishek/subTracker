import re
import logging
from typing import Dict, List, Tuple, Optional

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
      - Has improved handling of financial terms
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
        # Handle both dictionary and tuple row types
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
        list of keywords for each category.
        """
        keyword_patterns = {
            "Salary": ["salary", "wages", "pay", "payroll", "employment", "income salary"],
            "Investment Returns": [
                "investment return", "mutual fund", "mf", "returns", "dividend",
                "interest", "investment income", "redemption", "redeemed"
            ],
            "Freelance": ["freelance", "contract", "consulting", "project", "gig", "freelance income"],
            "Other Income": ["other", "miscellaneous", "misc", "other income"],
            "Deposit": ["deposit", "cash deposit", "bank deposit"],
            "Food & Dining": [
                "grocery", "groceries", "food", "dining", "restaurant", "swiggy",
                "zomato", "supermarket", "mart", "bazaar", "food delivery"
            ],
            "Utilities": [
                "electricity", "electric", "power", "utility", "utilities", "internet",
                "broadband", "water", "gas", "bill payment", "utility bill"
            ],
            "Transportation": [
                "transport", "fuel", "petrol", "diesel", "gas", "metro", "bus",
                "taxi", "uber", "ola", "travel", "transportation", "cab ride"
            ],
            "Health": [
                "health", "medical", "doctor", "hospital", "pharmacy", "medicine",
                "healthcare", "clinic", "prescription", "fitness", "gym", "medical expense"
            ],
            "Shopping": [
                "shopping", "purchase", "store", "retail", "amazon", "flipkart",
                "mall", "market", "buy", "online", "shop", "shopping expense"
            ],
            "EMI & Payments": [
                "emi", "loan", "payment", "credit", "card", "mortgage", "debt",
                "installment", "finance", "insurance", "repaid", "friend",
                "loan emi", "credit card payment", "loan payment", "personal loan"
            ],
            "Investment": [
                "investment", "invest", "mutual fund", "mf", "stocks", "shares",
                "securities", "sip", "portfolio", "redeemed", "investment purchase"
            ],
            "Entertainment": [
                "entertainment", "movie", "theatre", "recreation", "game",
                "sports", "leisure", "fun", "entertainment expense"
            ],
            "Internal Transfer": [
                "transfer", "mov", "internal", "account transfer",
                "internal movement", "between accounts"
            ]
        }

        cat_keywords = {}
        for c_id, info in self.categories.items():
            name = info["name"].strip().lower()
            desc = (info["description"] or "").strip().lower()

            # Start with words from name + description
            words = self._clean_text(name).split()
            if desc:
                words += self._clean_text(desc).split()

            # Add known patterns if name matches
            for pattern_name, synonyms in keyword_patterns.items():
                if pattern_name.lower() in name:
                    for phrase in synonyms:
                        words += self._clean_text(phrase).split()

            # Add compound variations for special categories
            if "emi" in name.lower() or "payment" in name.lower():
                words.extend([
                    "emi", "loan-emi", "loan_emi", "loan payment",
                    "monthly payment", "installment"
                ])

            # De-duplicate
            cat_keywords[c_id] = list(set(words))

        return cat_keywords

    def _clean_text(self, text: str) -> str:
        """
        Lowercase, remove punctuation, compress whitespace.
        """
        if not text:
            return ""
        text = text.lower()

        # Keep common financial terms
        preserved_terms = {
            'emi': ' emi ',
            'loan-emi': ' loan emi ',
            'credit-card': ' credit card ',
            'mutual-fund': ' mutual fund ',
            'sip': ' sip '
        }

        for term, replacement in preserved_terms.items():
            text = text.replace(term, replacement)

        # Remove special characters but preserve hyphen for compound words
        text = re.sub(r'[^\w\s-]', ' ', text)

        # Handle hyphenated words
        text = re.sub(r'-+', ' ', text)

        return ' '.join(text.split())

    def _calculate_match_score(self, text: str, category_id: int) -> float:
        """
        Calculate match score with enhanced handling of key financial terms.
        """
        text_words = set(self._clean_text(text).split())
        cat_words = set(self.category_keywords.get(category_id, []))

        if not cat_words:
            return 0.0

        overlap = text_words & cat_words
        if not overlap:
            return 0.0

        # Give bonus score for key financial terms
        key_terms = {'emi', 'loan', 'credit', 'payment', 'salary', 'investment'}
        key_term_matches = len(overlap & key_terms)
        bonus = key_term_matches * 0.1  # little increacr in score to serve key term match

        # Calculate base score using word overlap
        base_score = len(overlap) / max(len(text_words), len(cat_words))

        # Add bonus and cap at 1.0
        return min(base_score + bonus, 1.0)

    def match_category(self, description: str, account: str, trans_type: str, threshold: float = 0.2) -> Tuple[
        int, float]:
        """
        Find best matching category with enhanced matching logic.
        """
        # Combine description and account for matching
        search_text = f"{description} {account}"
        logger.info(f"Matching category for: '{search_text}' (type: {trans_type})")

        # Get relevant categories
        matching_categories = self._get_categories_by_type(trans_type)

        # Find best match
        best_category_id = None
        best_match_score = 0.0

        for category_id, category_info in matching_categories.items():
            match_score = self._calculate_match_score(search_text, category_id)
            logger.debug(f"Score {match_score:.3f} for {category_info['name']}")

            if match_score > best_match_score:
                best_match_score = match_score
                best_category_id = category_id

        # Use default if no good match
        if not best_category_id or best_match_score < threshold:
            default_id = self._get_default_category(trans_type)
            if default_id is not None:
                best_category_id = default_id
            elif matching_categories:
                best_category_id = next(iter(matching_categories))
            best_match_score = 0.0

        self._log_match_result(description, best_category_id, best_match_score)
        return best_category_id, best_match_score

    def _get_categories_by_type(self, trans_type: str) -> Dict[int, Dict]:
        """Get categories matching transaction type."""
        return {
            cat_id: cat_info
            for cat_id, cat_info in self.categories.items()
            if cat_info["type"].lower() == trans_type.lower()
        }

    def _get_default_category(self, trans_type: str) -> Optional[int]:
        """
        Get default category ID based on transaction type.
        For expenses:
            - Use 'Other Expense' if exists for unmatched transactions
            - Fallback to 'Shopping' if 'Other Expense' not found
        For income:
            - Use 'Other Income' for unmatched transactions
        """
        trans_type = trans_type.lower()
        if trans_type == "expense":
            # First try to find "Other Expense" category
            other_expense = next(
                (cat_id for cat_id, cat_info in self.categories.items()
                 if cat_info["type"].lower() == "expense"
                 and cat_info["name"].lower() == "other expense"),
                None
            )
            if other_expense is not None:
                return other_expense

            # If "Other Expense" not found, fallback to "Shopping"
            return next(
                (cat_id for cat_id, cat_info in self.categories.items()
                 if cat_info["type"].lower() == "expense"
                 and cat_info["name"].lower() == "shopping"),
                None
            )
        else:
            # For income, use "Other Income"
            return next(
                (cat_id for cat_id, cat_info in self.categories.items()
                 if cat_info["type"].lower() == "income"
                 and cat_info["name"].lower() == "other income"),
                None
            )

    def _log_match_result(self, description: str, category_id: int, score: float) -> None:
        """Log the category matching result."""
        category_name = self.categories[category_id]["name"] if category_id else "Unknown"
        logger.info(f"Matched '{description}' to category '{category_name}' (score: {score:.2f})")