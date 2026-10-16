import sqlite3

DB_FILE = "institute_ops.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.executescript("""
    DROP TABLE IF EXISTS attendance_logs;
    DROP TABLE IF EXISTS fee_installments;
    DROP TABLE IF EXISTS enrollments;
    DROP TABLE IF EXISTS batches;
    DROP TABLE IF EXISTS users;

    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('student', 'trainer', 'counselor', 'accounts', 'owner'))
    );

    CREATE TABLE batches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        trainer_id TEXT,
        FOREIGN KEY (trainer_id) REFERENCES users(id)
    );

    CREATE TABLE enrollments (
        id TEXT PRIMARY KEY,
        batch_id TEXT,
        student_id TEXT,
        FOREIGN KEY (batch_id) REFERENCES batches(id),
        FOREIGN KEY (student_id) REFERENCES users(id)
    );

    CREATE TABLE attendance_logs (
        id TEXT PRIMARY KEY,
        batch_id TEXT,
        student_id TEXT,
        session_date TEXT,
        status TEXT CHECK(status IN ('present', 'absent', 'late')),
        FOREIGN KEY (student_id) REFERENCES users(id)
    );

    CREATE TABLE fee_installments (
        id TEXT PRIMARY KEY,
        enrollment_id TEXT,
        installment_num INT,
        amount REAL,
        due_date TEXT,
        status TEXT CHECK(status IN ('paid', 'pending', 'overdue')),
        FOREIGN KEY (enrollment_id) REFERENCES enrollments(id)
    );
    """)

    # Seed Data
    users = [
        ('u_tr1', 'Amit Sharma', 'amit@institute.com', '+919876543210', 'trainer'),
        ('u_s1', 'Rohan Verma', 'rohan@gmail.com', '+919123456780', 'student'),
        ('u_s2', 'Priya Kulkarni', 'priya@gmail.com', '+919123456781', 'student'),
        ('u_s3', 'Rahul Deshmukh', 'rahul@gmail.com', '+919123456782', 'student')
    ]
    cursor.executemany("INSERT INTO users VALUES (?,?,?,?,?)", users)

    cursor.execute("INSERT INTO batches VALUES ('b_java', 'Java Fullstack Weekday', 'u_tr1')")
    
    enrollments = [
        ('en_1', 'b_java', 'u_s1'),
        ('en_2', 'b_java', 'u_s2'),
        ('en_3', 'b_java', 'u_s3')
    ]
    cursor.executemany("INSERT INTO enrollments VALUES (?,?,?)", enrollments)

    # Attendance logs (Rohan: 3 absences, Priya: 1 absence, Rahul: 3 absences)
    attendance = [
        ('att_1', 'b_java', 'u_s1', '2026-10-01', 'absent'),
        ('att_2', 'b_java', 'u_s1', '2026-10-02', 'absent'),
        ('att_3', 'b_java', 'u_s1', '2026-10-03', 'absent'),
        ('att_4', 'b_java', 'u_s2', '2026-10-01', 'absent'),
        ('att_5', 'b_java', 'u_s2', '2026-10-02', 'present'),
        ('att_6', 'b_java', 'u_s3', '2026-10-01', 'absent'),
        ('att_7', 'b_java', 'u_s3', '2026-10-02', 'absent'),
        ('att_8', 'b_java', 'u_s3', '2026-10-03', 'absent'),
    ]
    cursor.executemany("INSERT INTO attendance_logs VALUES (?,?,?,?,?)", attendance)

    # Fee installments (Rohan has an overdue EMI, Rahul is fully paid)
    fees = [
        ('fee_1', 'en_1', 1, 6500.0, '2026-10-10', 'overdue'),
        ('fee_2', 'en_2', 1, 6500.0, '2026-10-10', 'paid'),
        ('fee_3', 'en_3', 1, 6500.0, '2026-10-10', 'paid')
    ]
    cursor.executemany("INSERT INTO fee_installments VALUES (?,?,?,?,?,?)", fees)

    conn.commit()
    conn.close()
    print("✓ Local database initialized and seeded successfully.")

def run_query(sql: str):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(sql)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

if __name__ == "__main__":
    init_db()