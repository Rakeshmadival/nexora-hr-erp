from werkzeug.security import generate_password_hash
from database import get_db_connection

username = "hradmin"
password = "Admin@123"

password_hash = generate_password_hash(password)

db = get_db_connection()
cursor = db.cursor()

cursor.execute("""
    INSERT INTO users (username, password_hash, role)
    VALUES (%s, %s, %s)
""", (username, password_hash, "HR Admin"))

db.commit()

cursor.close()
db.close()

print("HR Admin account created successfully.")
