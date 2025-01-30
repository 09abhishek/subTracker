from decimal import Decimal
from typing import List, Annotated, Optional, Dict, Tuple
import mysql.connector

class CategoryMatcher:
    """
    Enhanced category matching system that uses database categories and synonym matching
    to accurately map transaction descriptions to category IDs.
    """

    def __init__(self, db_cursor: mysql.connector.MySQLConnection):
        self.cursor = db_cursor
        # Load categories from DB immediately
        self.categories = self._load_categories()
        self.category_keywords = self._initialize_keyword_mappings()

    def _load_categories(self) -> Dict[int, Dict]:
        """Load categories from database into a dictionary"""
        self.cursor.execute("""
            SELECT id, name, type, description 
            FROM categories 
            ORDER BY id
        """)
        return {
            row[0]: {
                'id': row[0],
                'name': row[1],
                'type': row[2],
                'description': row[3]
            }
            for row in self.cursor.fetchall()
        }

    def _initialize_keyword_mappings(self) -> Dict[int, List[str]]:
        """Initialize keyword mappings for each category ID"""
        mappings = {}

        # Common keywords for each category type
        keyword_patterns = {
            # Income categories
            'Salary': ['salary', 'wages', 'pay', 'payroll', 'employment'],
            'Investment Returns': ['investment return', 'mutual fund', 'mf', 'returns', 'dividend', 'interest'],
            'Freelance': ['freelance', 'contract', 'consulting', 'project', 'gig'],
            'Other Income': ['other', 'miscellaneous', 'misc'],
            'Deposit': ['deposit', 'cash deposit', 'bank deposit'],

            # Expense categories
            'Food & Dining': [
                'grocery', 'groceries', 'food', 'dining', 'restaurant',
                'swiggy', 'zomato', 'supermarket', 'mart', 'bazaar'
            ],
            'Utilities': [
                'electricity', 'electric', 'power', 'utility', 'utilities',
                'internet', 'broadband', 'water', 'gas', 'bill payment'
            ],
            'Transportation': [
                'transport', 'fuel', 'petrol', 'diesel', 'gas', 'metro',
                'bus', 'taxi', 'uber', 'ola', 'travel'
            ],
            'Health': [
                'health', 'medical', 'doctor', 'hospital', 'pharmacy',
                'medicine', 'healthcare', 'clinic', 'prescription'
            ],
            'Shopping': [
                'shopping', 'purchase', 'store', 'retail', 'amazon',
                'flipkart', 'mall', 'market', 'buy'
            ],
            'EMI & Payments': [
                'emi', 'loan', 'payment', 'credit card', 'mortgage',
                'debt', 'installment', 'finance'
            ],
            'Investment': [
                'investment', 'invest', 'mutual fund', 'mf', 'stocks',
                'shares', 'securities', 'sip', 'portfolio'
            ],
            'Entertainment': [
                'entertainment', 'movie', 'theatre', 'recreation',
                'game', 'sports', 'leisure', 'fun'
            ],
            'Internal Transfer': ['transfer', 'mov', 'internal', 'account transfer']
        }

        # Map keywords to category IDs based on name matching
        for cat_id, category in self.categories.items():
            cat_name = category['name']
            keywords = []

            # Add the category name itself as a keyword
            keywords.append(cat_name.lower())

            # Add words from the category name
            keywords.extend(cat_name.lower().split())

            # Add words from the description
            if category['description']:
                keywords.extend(category['description'].lower().split())

            # Add predefined keywords if they exist for this category
            for pattern, pattern_keywords in keyword_patterns.items():
                if pattern.lower() in cat_name.lower():
                    keywords.extend(pattern_keywords)

            # Store unique keywords for this category
            mappings[cat_id] = list(set(keywords))

        return mappings

    def _clean_text(self, text: str) -> str:
        """Clean and normalize text for matching"""
        text = text.lower()
        text = re.sub(r'[^\w\s]', ' ', text)
        return ' '.join(text.split())

    def _calculate_match_score(self, text: str, category_id: int) -> float:
        """Calculate match score between text and category keywords"""
        text = self._clean_text(text)
        keywords = self.category_keywords.get(category_id, [])

        max_score = 0
        text_words = set(text.split())

        for keyword in keywords:
            # Exact keyword match
            if keyword in text:
                return 1.0

            # Word-level matching
            keyword_words = set(keyword.split())
            if keyword_words & text_words:  # If there's any word overlap
                score = len(keyword_words & text_words) / max(len(keyword_words), len(text_words))
                max_score = max(max_score, score)

        return max_score

    def match_category(
            self,
            description: str,
            account: str,
            trans_type: str,
            threshold: float = 0.3
    ) -> Tuple[int, float]:
        """
        Match transaction to category using description and account
        Returns tuple of (category_id, confidence_score)
        """
        best_match_id = None
        best_match_score = 0

        # Get relevant categories for transaction type
        relevant_categories = {
            cat_id: cat for cat_id, cat in self.categories.items()
            if cat['type'] == trans_type
        }

        # Combine description and account for matching
        text_to_match = f"{description} {account}"

        for cat_id, category in relevant_categories.items():
            # Calculate match score
            score = self._calculate_match_score(text_to_match, cat_id)

            if score > best_match_score:
                best_match_score = score
                best_match_id = cat_id

        # Return default category if no good match
        if best_match_score < threshold:
            # Default to "Other Income" (id: 4) or "Shopping" (id: 9)
            default_id = next(
                (cat_id for cat_id, cat in self.categories.items()
                 if cat['name'] in ['Other Income', 'Shopping'] and cat['type'] == trans_type),
                4 if trans_type == 'income' else 9
            )
            return default_id, 0.0

        return best_match_id, best_match_score


def determine_category(
        description: str,
        account: str,
        trans_type: str,
        db_cursor: mysql.connector.MySQLConnection
) -> int:
    """
    Enhanced category matching function that uses database categories
    Returns category ID for the transaction
    """
    matcher = CategoryMatcher(db_cursor)
    category_id, confidence = matcher.match_category(description, account, trans_type)
    return category_id