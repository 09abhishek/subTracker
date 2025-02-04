def get_category_by_id(cursor, category_id: int):
    cursor.execute(
        "SELECT * FROM categories WHERE id = %s",
        (category_id,)
    )
    return cursor.fetchone()