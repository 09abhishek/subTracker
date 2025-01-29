from datetime import datetime
import re
from typing import List, Dict


def parse_amount(amount_str: str) -> str:
    """Parse and clean amount string"""
    if not amount_str:
        return None
    # Remove extra spaces and return cleaned amount
    return amount_str.strip()


def calculate_inverse_amount(amount_str: str) -> str:
    """Calculate inverse of amount for balancing posting"""
    if not amount_str:
        return None
    # Remove ₹ symbol and spaces
    cleaned = amount_str.replace('₹', '').strip()
    try:
        # Convert to float
        amount = float(cleaned)
        # Return inverse with ₹ symbol
        return f"₹{-amount:.2f}"
    except ValueError:
        return None