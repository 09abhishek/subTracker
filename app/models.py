from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, EmailStr
from typing import List, Annotated
from datetime import datetime
from datetime import date
from decimal import Decimal
from typing import Optional

class Posting(BaseModel):
    account: str
    amount: Optional[str] = None


class Transaction(BaseModel):
    date: date
    description: str
    postings: List[Posting]


class LedgerResponse(BaseModel):
    transactions: List[Transaction]
    message: str
    status: str


# Add this to your models section
class BalanceResponse(BaseModel):
    # User details
    email: str
    phone: str
    full_name: str

    # Account details
    account_name: str
    current_balance: Decimal = Field(decimal_places=2)
    model_config = ConfigDict(from_attributes=True)

class TransactionType(str, Enum):
    income = "income"
    expense = "expense"
    transfer = "transfer"

class CategoryBase(BaseModel):
    name: str = Field(..., max_length=100)
    type: TransactionType
    description: Optional[str] = Field(None, max_length=255)


class ExpenseCreate(BaseModel):
    amount: Decimal = Field(..., gt=0, description="Positive amount for the expense")
    category_id: int = Field(..., description="Category ID for the expense")
    description: str = Field(..., min_length=1, max_length=255)
    transaction_date: date = Field(default_factory=date.today)

    model_config = ConfigDict(from_attributes=True)

class BalanceUpdate(BaseModel):
    bank_account_id: int
    amount: Decimal = Field(..., description="Amount to update. Positive for credit, negative for debit")
    transaction_type: TransactionType
    category_id: int
    description: str = Field(..., min_length=1, max_length=255)

class TransactionSource(str, Enum):
    manual = "manual"
    import_ = "import"


# Category Models
class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# Bank Account Models
class BankAccountBase(BaseModel):
    account_name: str = Field(..., max_length=255)
    current_balance: Decimal = Field(default=Decimal('0.00'), decimal_places=2)


class BankAccountCreate(BankAccountBase):
    pass


class BankAccountResponse(BankAccountBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# Transaction Models
class TransactionBase(BaseModel):
    bank_account_id: int
    category_id: int
    date: date
    description: str = Field(..., max_length=255)
    amount: Decimal = Field(..., decimal_places=2)
    source: TransactionSource = TransactionSource.manual


class TransactionCreate(TransactionBase):
    pass


class TransactionResponse(TransactionBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# User Models
class UserCreate(BaseModel):
    email: EmailStr
    phone: Annotated[str, Field(pattern=r'^\+?1?\d{9,15}$')]
    password: Annotated[str, Field(min_length=4)]
    full_name: Annotated[str, Field(min_length=1, max_length=255)]
    bank_name: Annotated[str, Field(min_length=1, max_length=255)]

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    id: int
    email: str
    phone: str
    full_name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str

    model_config = ConfigDict(from_attributes=True)


class TokenData(BaseModel):
    email: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

