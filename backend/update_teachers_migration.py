"""
Migration script to update teachers table with designation and correct is_internal flag.
Only 7 teachers are internal; all others are external guest faculty.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "dev.db")

INTERNAL_FACULTY = {
    "SKS": {"full_name": "Dr. Sanjit Kumar Setua", "designation": "Associate Professor"},
    "RKP": {"full_name": "Dr. Rajat Kumar Pal", "designation": "Professor"},
    "SC":  {"full_name": "Dr. Sankhayan Choudhury", "designation": "Professor"},
    "NC":  {"full_name": "Dr. Nabendu Chaki", "designation": "Professor"},
    "RD":  {"full_name": "Dr. Rajib Kumar Das", "designation": "Associate Professor & Head of Department"},
    "PB":  {"full_name": "Dr. Pritha Banerjee", "designation": "Assistant Professor"},
    "SK":  {"full_name": "Dr. Sunirmal Khatua", "designation": "Assistant Professor"},
}

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Add designation column if not exists
    cursor.execute("PRAGMA table_info(teachers)")
    columns = [row[1] for row in cursor.fetchall()]
    if "designation" not in columns:
        print("Adding designation column to teachers table...")
        cursor.execute("ALTER TABLE teachers ADD COLUMN designation VARCHAR(100) DEFAULT NULL")
        conn.commit()

    # 2. Update all teachers to external guest faculty first
    cursor.execute("UPDATE teachers SET is_internal = 0, designation = 'External Guest Faculty'")
    conn.commit()

    # 3. Set the 7 internal faculty
    for code, info in INTERNAL_FACULTY.items():
        cursor.execute(
            "UPDATE teachers SET is_internal = 1, designation = ?, full_name = ? WHERE short_name = ?",
            (info["designation"], info["full_name"], code)
        )
        if cursor.rowcount == 0:
            print(f"Warning: Teacher {code} not found in DB")
        else:
            print(f"Set internal teacher {code} -> {info['designation']}")

    conn.commit()

    # 4. Print summary
    print("\n--- Summary of Internal Faculty ---")
    cursor.execute("SELECT id, short_name, full_name, designation, is_internal FROM teachers WHERE is_internal = 1")
    internals = cursor.fetchall()
    for row in internals:
        print(f"  [{row[1]}] {row[2]} — {row[3]} (is_internal={row[4]})")
    print(f"Total internal teachers: {len(internals)}")

    cursor.execute("SELECT COUNT(*) FROM teachers WHERE is_internal = 0")
    external_count = cursor.fetchone()[0]
    print(f"Total external guest faculty: {external_count}")

    conn.close()

if __name__ == "__main__":
    migrate()
