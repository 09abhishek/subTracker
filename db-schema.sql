-- Create the schema
CREATE SCHEMA IF NOT EXISTS sub_tracker;
USE sub_tracker;

-- Users table
CREATE TABLE users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login TIMESTAMP NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Store JWT refresh tokens
CREATE TABLE auth_tokens (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    refresh_token VARCHAR(255) NOT NULL,
    access_token VARCHAR(500) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Bank Account table (single account per user)
CREATE TABLE bank_accounts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    account_name VARCHAR(255) NOT NULL,
    account_type ENUM('savings', 'current') NOT NULL DEFAULT 'savings',
    current_balance DECIMAL(15, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'INR',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Categories table with more flexibility
CREATE TABLE categories (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    type ENUM('income', 'expense', 'transfer') NOT NULL,
    description VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Transactions table with enhanced tracking
CREATE TABLE transactions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    bank_account_id BIGINT NOT NULL,
    category_id BIGINT NOT NULL,
    date DATE NOT NULL,
    description VARCHAR(255) NOT NULL,
    amount DECIMAL(15, 2) NOT NULL,  -- Positive for income, Negative for expense
    type ENUM('income', 'expense', 'transfer') NOT NULL,
    debit_account VARCHAR(255),
    credit_account VARCHAR(255),
    source VARCHAR(50) DEFAULT 'manual',  -- 'manual' or 'import'
    notes VARCHAR(255),
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Indexes for performance optimization
-- User and Authentication Indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_auth_tokens_user ON auth_tokens(user_id);

-- Bank Account Indexes
CREATE INDEX idx_bank_accounts_user ON bank_accounts(user_id);

-- Categories Indexes
CREATE INDEX idx_categories_type ON categories(type);

-- Transaction Indexes
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_user ON transactions(user_id);
CREATE INDEX idx_transactions_category ON transactions(category_id);
CREATE INDEX idx_transactions_type ON transactions(type);
CREATE INDEX idx_transactions_debit_account ON transactions(debit_account);
CREATE INDEX idx_transactions_credit_account ON transactions(credit_account);

-- Composite Indexes for Frequent Queries
CREATE INDEX idx_transactions_date_type ON transactions(date, type);
CREATE INDEX idx_transactions_user_category ON transactions(user_id, category_id);
CREATE INDEX idx_transactions_user_date_type ON transactions(user_id, date, type);

-- Insert default categories
INSERT INTO categories (name, type, description) VALUES
-- Income Categories
('Salary', 'income', 'Regular employment income'),
('Investment Returns', 'income', 'Returns from mutual funds and investments'),
('Freelance', 'income', 'Freelance and project-based income'),
('Other Income', 'income', 'Miscellaneous income'),
('Deposit', 'income', 'Cash deposit to self account'),

-- Expense Categories
('Food & Dining', 'expense', 'Groceries and restaurants'),
('Utilities', 'expense', 'Electricity, internet, and bills'),
('Transportation', 'expense', 'Fuel and travel expenses'),
('Health', 'expense', 'Medical and pharmacy expenses'),
('Shopping', 'expense', 'Online and offline shopping'),
('EMI & Payments', 'expense', 'Loan EMIs and credit card payments'),
('Investment', 'expense', 'Mutual funds and investments'),
('Entertainment', 'expense', 'Leisure and recreational expenses'),
('Other Expense', 'expense', 'Miscellaneous expense'),

-- Transfer Categories
('Internal Transfer', 'transfer', 'Account transfers');