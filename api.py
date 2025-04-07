from flask import Flask, jsonify, request
from flask_mysqldb import MySQL
from flask_cors import CORS
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import credentials, messaging
import os

app = Flask(__name__)
CORS(app)  # Allow React/React Native to connect

# MySQL Config (XAMPP Defaults)
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'library_management_system'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'  # This is the key change

mysql = MySQL(app)


LIBRARIANS = {
    "admin@college.com": "lib123",
    "librarian@college.com": "lib456"
}


# Initialize Firebase Admin SDK
cred = credentials.Certificate("library-management-syste-ae8eb-firebase-adminsdk-fbsvc-98cdc23c65.json")
firebase_admin.initialize_app(cred)



def calculate_status(quantity):
    if quantity > 5:
        return 'Available'
    elif quantity > 0:
        return 'Low Stock'
    else:
        return 'Out of Stock'


@app.route('/dashboard-stats', methods=['GET'])
def get_dashboard_stats():
    try:
        cur = mysql.connection.cursor()
        
        # Total books
        cur.execute("SELECT COUNT(*) as total_books FROM books")
        total_books = cur.fetchone()['total_books']
        
        # Available books
        cur.execute("SELECT COUNT(*) as available_books FROM books WHERE quantity > 0")
        available_books = cur.fetchone()['available_books']
        
        # Total students
        cur.execute("SELECT COUNT(*) as total_students FROM students")
        total_students = cur.fetchone()['total_students']
        
        # Currently issued books
        cur.execute("SELECT COUNT(*) as issued_books FROM book_issues WHERE status = 'Issued'")
        issued_books = cur.fetchone()['issued_books']
        
        # Overdue books
        today = datetime.now().strftime('%Y-%m-%d')
        cur.execute("""
            SELECT COUNT(*) as overdue_books 
            FROM book_issues 
            WHERE status = 'Issued' AND due_date < %s
        """, (today,))
        overdue_books = cur.fetchone()['overdue_books']
        
        cur.close()
        
        return jsonify({
            'total_books': total_books,
            'available_books': available_books,
            'total_students': total_students,
            'issued_books': issued_books,
            'overdue_books': overdue_books
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    


@app.route('/classes', methods=['GET'])
def get_classes():
    try:
        cur = mysql.connection.cursor()
        
        # Get distinct classes from students table
        cur.execute("SELECT DISTINCT class FROM students ORDER BY class")
        classes = [row['class'] for row in cur.fetchall()]
        
        # Get distinct classes from books table (if needed)
        cur.execute("SELECT DISTINCT class FROM books ORDER BY class")
        book_classes = [row['class'] for row in cur.fetchall()]
        
        # Combine and dedupe
        all_classes = list(set(classes + book_classes))
        all_classes.sort()
        
        cur.close()
        
        return jsonify({
            'classes': all_classes,
            'count': len(all_classes)
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    



# Login Route (No sessions/tokens)
@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"success": False, "error": "Email/password missing"}), 400

    if email in LIBRARIANS and LIBRARIANS[email] == password:
        return jsonify({"success": True, "user_type": "librarian", "redirect": "/dashboard"})
    else:
        # Check student login
        cur = mysql.connection.cursor()
        cur.execute("SELECT student_id FROM students WHERE email = %s AND password = %s", (email, password))
        student = cur.fetchone()
        if student:
            return jsonify({"success": True, "user_type": "student", "student_id": student['student_id'], "redirect": "/dashboard"})
        return jsonify({"success": False, "error": "Invalid credentials"}), 401
    


# API 1: Get books by class (BCA/BFA/BCOM)
@app.route('/books/<class_name>', methods=['GET'])
def get_books(class_name):
    cur = mysql.connection.cursor()
    cur.execute("SELECT * FROM books WHERE class = %s", (class_name,))
    books = cur.fetchall()
    return jsonify(books)



# Get Book From Database
@app.route('/books', methods=['GET'])
def get_all_books():
    try:
        cur = mysql.connection.cursor()
        
        # Get all books with required fields - using dictionary cursor
        # cur = mysql.connection.cursor(dictionary=True)
        cur.execute("""
            SELECT book_id as id, title, author, subject, class, 
                   quantity, semester 
            FROM books
        """)
        books = cur.fetchall()
        # Calculate status for each book
        for book in books:
            book['status'] = calculate_status(book['quantity'])
            book['isbn'] = f"ISBN-{book['id']:010d}"  # Generate dummy ISBN
        
        # Get unique values for filters
        cur.execute("SELECT DISTINCT class FROM books")
        classes = [row['class'] for row in cur.fetchall()]
        
        cur.execute("SELECT DISTINCT subject FROM books")
        subjects = [row['subject'] for row in cur.fetchall()]
        
        statuses = ['Available', 'Low Stock', 'Out of Stock']
        
        # Count stats - note the correct column name (quantity vs qunatity)
        cur.execute("""
            SELECT 
                SUM(quantity > 5) as available,
                SUM(quantity > 0 AND quantity <= 5) as low_stock,
                SUM(quantity = 0) as out_of_stock,
                COUNT(*) as total 
            FROM books
        """)
        stats = cur.fetchone()
        
        cur.close()
        
        response = {
            'books': books,
            'filters': {
                'classes': classes,
                'subjects': subjects,
                'statuses': statuses
            },
            'stats': stats
        }
        
        return jsonify(response)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

#Get Book By Book Id
@app.route('/books/<int:book_id>', methods=['GET'])
def get_book_id(book_id):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT book_id as id, title, authour as author, subject, class, 
                   qunatity as quantity, semester 
            FROM books 
            WHERE book_id = %s
        """, (book_id,))
        book = cur.fetchone()
        cur.close()
        
        if book:
            book['status'] = calculate_status(book['quantity'])
            book['isbn'] = f"ISBN-{book['id']:010d}"  # Generate dummy ISBN
            return jsonify(book)
        return jsonify({'error': 'Book not found'}), 404
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Add Book to Database
@app.route('/add-book', methods=['POST'])
def add_book():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'author', 'class', 'quantity', 'semester','subject']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({"success": False, "error": f"Missing or empty field: {field}"}), 400

        # Insert into database
        cur = mysql.connection.cursor()
        cur.execute(
            """INSERT INTO books 
            (title, author, class, quantity, semester, subject) 
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (data['title'], data['author'], data['class'], data['quantity'], data['semester'], data['subject'])
        )
        mysql.connection.commit()
        return jsonify({"success": True, "message": "Book added successfully!"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    



# Get All Students
@app.route('/students', methods=['GET'])
def get_all_students():
    try:
        cur = mysql.connection.cursor()
        
        # Get all students with required fields
        cur.execute("""
            SELECT 
                student_id, name, father_name, mobile_number, 
                guardian_mobile_number, class, admission_year, 
                roll_no, college_rollno
            FROM students
        """)
        students = cur.fetchall()
        
        # Get unique values for filters
        cur.execute("SELECT DISTINCT class FROM students")
        classes = [row['class'] for row in cur.fetchall()]
        
        cur.execute("SELECT DISTINCT admission_year FROM students")
        years = [row['admission_year'] for row in cur.fetchall()]
        
        # Count stats
        cur.execute("SELECT COUNT(*) as total FROM students")
        stats = cur.fetchone()
        
        cur.close()
        
        response = {
            'students': students,
            'filters': {
                'classes': classes,
                'years': years
            },
            'stats': {
                'total_students': stats['total']
            }
        }
        
        return jsonify(response)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

# Add Student to Database
@app.route('/add-student', methods=['POST'])
def add_student():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = [
            'name', 
            'father_name', 
            'class', 
            'admission_year', 
            'roll_no',
            'mobile_number',
            'guardian_mobile_number'
        ]
        
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({"success": False, "error": f"Missing or empty field: {field}"}), 400

        # Validate mobile numbers
        if not str(data['mobile_number']).isdigit() or len(str(data['mobile_number'])) != 10:
            return jsonify({"success": False, "error": "Mobile number must be 10 digits"}), 400
            
        if not str(data['guardian_mobile_number']).isdigit() or len(str(data['guardian_mobile_number'])) != 10:
            return jsonify({"success": False, "error": "Guardian mobile number must be 10 digits"}), 400

        # Insert into database
        cur = mysql.connection.cursor()
        cur.execute(
            """INSERT INTO students 
            (name, father_name, class, admission_year, roll_no, college_rollno,
             mobile_number, guardian_mobile_number) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                data['name'], 
                data['father_name'], 
                data['class'], 
                data['admission_year'], 
                data['roll_no'], 
                data['college_rollno'],
                data['mobile_number'],
                data['guardian_mobile_number']
            )
        )
        mysql.connection.commit()
        
        return jsonify({
            "success": True, 
            "message": "Student added successfully!",
            "college_rollno": data['college_rollno']
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@app.route('/books/<class_name>/<int:semester>', methods=['GET'])
def get_class_books(class_name, semester):
    try:
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT 
                book_id, title, author, subject, 
                quantity, semester, class
            FROM books 
            WHERE class = %s AND semester = %s
            ORDER BY subject
        """, (class_name, semester))
        books = cur.fetchall()
        
        # Calculate status for each book
        for book in books:
            book['status'] = 'Available' if book['quantity'] > 0 else 'Out of Stock'
            book['cover_image'] = 'https://via.placeholder.com/150'
            # f"https://covers.openlibrary.org/b/isbn/{book['isbn']}-M.jpg" if book['isbn'] else 'https://via.placeholder.com/150'
        
        return jsonify({
            'success': True,
            'books': books,
            'class': class_name,
            'semester': semester
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    

# @app.route('/student-login', methods=['POST'])
# def student_login():
#     try:
#         data = request.get_json()
#         email = data.get('email')
#         password = data.get('password')
#         device_info = data.get('device_info', {})  # {name, os, token}

#         if not email or not password:
#             return jsonify({"success": False, "error": "Email and password required"}), 400

#         cur = mysql.connection.cursor()
        
#         # 1. Verify student credentials
#         cur.execute("SELECT student_id FROM students WHERE email = %s AND password = %s", 
#                    (email, password))
#         student = cur.fetchone()
        
#         if not student:
#             return jsonify({"success": False, "error": "Invalid credentials"}), 401

#         student_id = student['student_id']
        
#         # 2. Update login info in student_logins table
#         now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
#         ip_address = request.remote_addr
        
#         # Check if device exists
#         cur.execute(
#             """SELECT id, login_count FROM student_logins 
#             WHERE student_id = %s AND device_token = %s""",
#             (student_id, device_info.get('token'))
#         )
#         existing_device = cur.fetchone()

#         if existing_device:
#             # Update existing device
#             cur.execute(
#                 """UPDATE student_logins SET 
#                 last_login = %s,
#                 device_name = %s,
#                 device_os = %s,
#                 ip_address = %s,
#                 login_count = login_count + 1
#                 WHERE id = %s""",
#                 (now, device_info.get('name'), device_info.get('os'), 
#                  ip_address, existing_device['id'])
#             )
#         else:
#             # Insert new device
#             cur.execute(
#                 """INSERT INTO student_logins 
#                 (student_id, last_login, device_name, device_os, device_token, ip_address)
#                 VALUES (%s, %s, %s, %s, %s, %s)""",
#                 (student_id, now, device_info.get('name'), device_info.get('os'), 
#                  device_info.get('token'), ip_address)
#             )
        
#         mysql.connection.commit()
        
#         return jsonify({
#             "success": True,
#             "student_id": student_id,
#             "message": "Login successful"
#         })

#     except Exception as e:
#         return jsonify({"success": False, "error": str(e)}), 500
    


@app.route('/student-login', methods=['POST'])
def student_login():
    try:
        data = request.get_json()
        mobile_number = data.get('mobile_number')
        device_info = data.get('device_info', {})  # {name, os, token}

        if not mobile_number :
            return jsonify({"success": False, "error": "Mobile number required"}), 400

        cur = mysql.connection.cursor()
        
        # 1. Verify student credentials using mobile number
        cur.execute("""
            SELECT student_id, name, class 
            FROM students 
            WHERE mobile_number = %s
        """, (mobile_number,))
        student = cur.fetchone()
        
        if not student:
            return jsonify({
                "success": False, 
                "error": "Mobile number not registered. Please register at the library.",
                "error_code": "not_registered"
            }), 401

        student_id = student['student_id']
        
        # 2. Update login info in student_logins table
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Check if device exists
        cur.execute(
            """SELECT id FROM student_logins 
            WHERE student_id = %s """,
            (student_id,)
        )
        existing_device = cur.fetchone()

        if existing_device:
            # Update existing device
            cur.execute(
                """UPDATE student_logins SET 
                last_login = %s,
                device_name = %s,
                device_os = %s,
                device_token = %s
                WHERE id = %s""",
                (now, device_info.get('name'), device_info.get('os'),device_info.get('token'), 
                 existing_device['id'])
            )
        else:
            # Insert new device
            cur.execute(
                """INSERT INTO student_logins 
                (student_id, register_on, last_login, device_name, device_os, device_token)
                VALUES (%s, %s, %s, %s, %s, %s)""",
                (student_id, now, now, device_info.get('name'), device_info.get('os'), 
                 device_info.get('token'))
            )
        
        mysql.connection.commit()
        
        return jsonify({
            "success": True,
            "student_id": student_id,
            "name": student['name'],
            "class": student['class'],
            "message": "Login successful"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    


@app.route('/student/<int:student_id>/upload-photo', methods=['POST'])
def upload_student_photo(student_id):
    try:
        if 'photo' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
            
        photo = request.files['photo']
        if photo.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
            
        # Save to uploads folder (create if doesn't exist)
        upload_folder = 'uploads/student_photos'
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)
            
        filename = f"{student_id}_{secure_filename(photo.filename)}"
        filepath = os.path.join(upload_folder, filename)
        photo.save(filepath)
        
        # Update database with file path
        cur = mysql.connection.cursor()
        cur.execute("""
            UPDATE students 
            SET photo_path = %s 
            WHERE student_id = %s
        """, (filepath, student_id))
        mysql.connection.commit()
        
        return jsonify({
            'success': True,
            'photo_url': f"/{filepath}"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    

# Issue Book API
@app.route('/issue-book', methods=['POST'])
def issue_book():
    try:
        data = request.get_json()
        student_id = data['student_id']
        book_id = data['book_id']
        issue_date = datetime.now().strftime('%Y-%m-%d')
        due_date = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')  # 2 weeks
        
        cur = mysql.connection.cursor()
        
        # Check book availability
        cur.execute("SELECT quantity FROM books WHERE book_id = %s", (book_id,))
        book = cur.fetchone()
        if not book or book['quantity'] <= 0:
            return jsonify({'error': 'Book not available'}), 400
        
        # Issue book
        cur.execute("""
            INSERT INTO book_issues 
            (student_id, book_id, issue_date, due_date, status) 
            VALUES (%s, %s, %s, %s, 'Issued')
        """, (student_id, book_id, issue_date, due_date))
        
        # Update book quantity
        cur.execute("UPDATE books SET quantity = quantity - 1 WHERE book_id = %s", (book_id,))
        
        mysql.connection.commit()
        cur.close()
        
        return jsonify({'message': 'Book issued successfully'})
    
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'error': str(e)}), 500

# Return Book API
@app.route('/return-book', methods=['POST'])
def return_book():
    try:
        data = request.get_json()
        issue_id = data.get('issue_id')
        student_id = data.get('student_id')
        
        if not issue_id or not student_id:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        cur = mysql.connection.cursor()
        
        # Verify the book belongs to this student
        cur.execute("""
            SELECT book_id FROM book_issues 
            WHERE issue_id = %s AND student_id = %s AND status = 'Issued'
        """, (issue_id, student_id))
        issue = cur.fetchone()
        
        if not issue:
            return jsonify({'success': False, 'error': 'No active issue found'}), 404
        
        # Calculate fine if overdue
        today = datetime.now().date()
        cur.execute("SELECT due_date FROM book_issues WHERE issue_id = %s", (issue_id,))
        due_date = cur.fetchone()['due_date']
        days_late = max(0, (today - due_date).days)
        fine = days_late * 10  # ₹10 per day
        
        # Update records
        cur.execute("""
            UPDATE book_issues 
            SET return_date = %s, 
                status = 'Returned',
                fine = %s
            WHERE issue_id = %s
        """, (today, fine, issue_id))
        
        cur.execute("""
            UPDATE books 
            SET quantity = quantity + 1 
            WHERE book_id = %s
        """, (issue['book_id'],))
        
        mysql.connection.commit()
        
        return jsonify({
            'success': True,
            'message': 'Book returned successfully',
            'fine': fine,
            'days_late': days_late
        })
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    



# Student-initiated return (marks as pending)
# @app.route('/student/initiate-return', methods=['POST'])
# def initiate_return():
#     try:
#         data = request.get_json()
#         student_id = data['student_id']
#         book_id = data['book_id']
        
#         cur = mysql.connection.cursor()
        
#         # Find the active issue
#         cur.execute("""
#             SELECT issue_id FROM book_issues 
#             WHERE student_id = %s AND book_id = %s AND status = 'Issued'
#             LIMIT 1
#         """, (student_id, book_id))
#         issue = cur.fetchone()
        
#         if not issue:
#             return jsonify({'success': False, 'error': 'No active issue found'}), 404
        
#         # Mark as return requested
#         cur.execute("""
#             UPDATE book_issues 
#             SET return_status = 'Pending',
#                 return_requested_at = NOW()
#             WHERE issue_id = %s
#         """, (issue['issue_id'],))
        
#         mysql.connection.commit()
        
#         # Notify librarian (implement your notification system)
#         send_notification_to_librarian(student_id, book_id)
        
#         return jsonify({'success': True, 'message': 'Return request submitted'})
        
#     except Exception as e:
#         mysql.connection.rollback()
#         return jsonify({'success': False, 'error': str(e)}), 500

# # Librarian confirmation endpoint
# @app.route('/librarian/confirm-return', methods=['POST'])
# def confirm_return():
#     try:
#         data = request.get_json()
#         issue_id = data['issue_id']
#         librarian_id = data['librarian_id']
        
#         cur = mysql.connection.cursor()
        
#         # Verify the pending return
#         cur.execute("""
#             SELECT book_id, due_date FROM book_issues 
#             WHERE issue_id = %s AND return_status = 'Pending'
#         """, (issue_id,))
#         issue = cur.fetchone()
        
#         if not issue:
#             return jsonify({'success': False, 'error': 'No pending return found'}), 404
        
#         # Calculate fine
#         today = datetime.now().date()
#         days_late = max(0, (today - issue['due_date']).days)
#         fine = days_late * 10  # ₹10 per day
        
#         # Complete the return
#         cur.execute("""
#             UPDATE book_issues 
#             SET return_date = %s,
#                 status = 'Returned',
#                 return_status = 'Completed',
#                 fine = %s,
#                 processed_by = %s
#             WHERE issue_id = %s
#         """, (today, fine, librarian_id, issue_id))
        
#         # Update book quantity
#         cur.execute("""
#             UPDATE books 
#             SET quantity = quantity + 1 
#             WHERE book_id = %s
#         """, (issue['book_id'],))
        
#         mysql.connection.commit()
        
#         # Notify student
#         send_notification_to_student(issue_id)
        
#         return jsonify({
#             'success': True,
#             'message': 'Return confirmed',
#             'fine': fine
#         })
        
#     except Exception as e:
#         mysql.connection.rollback()
#         return jsonify({'success': False, 'error': str(e)}), 500
    


# @app.route('/return-book', methods=['POST'])
# def return_book():
#     try:
#         data = request.get_json()
#         issue_id = data['issue_id']
#         return_date = datetime.now().strftime('%Y-%m-%d')
        
#         cur = mysql.connection.cursor()
        
#         # Get issue details
#         cur.execute("SELECT book_id FROM book_issues WHERE issue_id = %s", (issue_id,))
#         issue = cur.fetchone()
#         if not issue:
#             return jsonify({'error': 'Invalid issue ID'}), 400
        
#         # Update issue record
#         cur.execute("""
#             UPDATE book_issues 
#             SET return_date = %s, status = 'Returned' 
#             WHERE issue_id = %s
#         """, (return_date, issue_id))
        
#         # Update book quantity
#         cur.execute("UPDATE books SET quantity = quantity + 1 WHERE book_id = %s", (issue['book_id'],))
        
#         mysql.connection.commit()
#         cur.close()
        
#         return jsonify({'message': 'Book returned successfully'})
    
#     except Exception as e:
#         mysql.connection.rollback()
#         return jsonify({'error': str(e)}), 500
    



@app.route('/transactions', methods=['GET'])
def get_transactions():
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        search = request.args.get('search', '')
        
        cur = mysql.connection.cursor()
        
        base_query = """
            SELECT 
                bi.issue_id, bi.issue_date, bi.due_date, bi.return_date, bi.status,
                b.title as book_title, b.author as book_author,
                s.name as student_name, s.roll_no as student_roll
            FROM book_issues bi
            JOIN books b ON bi.book_id = b.book_id
            JOIN students s ON bi.student_id = s.student_id
            WHERE 1=1
        """
        
        if search:
            base_query += f"""
                AND (b.title LIKE '%{search}%' 
                OR b.author LIKE '%{search}%'
                OR s.name LIKE '%{search}%'
                OR s.roll_no LIKE '%{search}%')
            """
        
        # Count total
        count_query = "SELECT COUNT(*) as total FROM (" + base_query + ") as subquery"
        cur.execute(count_query)
        total = cur.fetchone()['total']
        
        # Pagination
        offset = (page - 1) * per_page
        base_query += f" ORDER BY bi.issue_date DESC LIMIT {per_page} OFFSET {offset}"
        
        cur.execute(base_query)
        transactions = cur.fetchall()
        cur.close()
        
        return jsonify({
            'transactions': transactions,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total
            }
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    


@app.route('/overdue-books', methods=['GET'])
def get_overdue_books():
    try:
        cur = mysql.connection.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        cur.execute("""
            SELECT 
                bi.issue_id, bi.due_date,
                b.title as book_title, b.author as book_author,
                s.name as student_name, s.roll_no, s.mobile_number
            FROM book_issues bi
            JOIN books b ON bi.book_id = b.book_id
            JOIN students s ON bi.student_id = s.student_id
            WHERE bi.status = 'Issued' AND bi.due_date < %s
            ORDER BY bi.due_date
        """, (today,))
        
        overdue_books = cur.fetchall()
        cur.close()
        
        return jsonify({'overdue_books': overdue_books})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    


# GET endpoint to search students by class/semester
@app.route('/students/search', methods=['GET'])
def search_students():
    try:
        class_filter = request.args.get('class')
        semester_filter = request.args.get('semester')
        search_term = request.args.get('search', '')
        current_year = datetime.now().year
        current_month = datetime.now().month
        
        # Calculate current semester (assuming 2 semesters per year)
        current_semester = 1 if current_month <= 6 else 2
        
        query = """
            SELECT 
                s.student_id, 
                s.name, 
                s.roll_no, 
                s.class, 
                s.admission_year,
                /* Calculate current semester */
                ((%s - s.admission_year) * 2 + %s) AS current_semester,
                (SELECT COUNT(*) FROM book_issues bi 
                 WHERE bi.student_id = s.student_id AND bi.status = 'Issued') as issued_count
            FROM students s
            WHERE 1=1
        """
        
        # Start with these parameters for semester calculation
        params = [current_year, current_semester]
        
        if class_filter:
            query += " AND s.class = %s"
            params.append(class_filter)
            
        if semester_filter:
            # Compare against calculated semester
            query += " AND ((%s - s.admission_year) * 2 + %s) = %s"
            params.extend([current_year, current_semester, semester_filter])
            
        if search_term:
            query += " AND (s.name LIKE %s OR s.roll_no LIKE %s)"
            params.extend([f"%{search_term}%", f"%{search_term}%"])
            
        query += " ORDER BY s.name LIMIT 20"
        
        cur = mysql.connection.cursor()
        cur.execute(query, params)
        students = cur.fetchall()
        cur.close()
        
        return jsonify(students)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

@app.route('/books/issue', methods=['POST'])
def book_issue_book():
    try:
        data = request.get_json()
        student_id = data['student_id']
        book_id = data['book_id']
        current_date = datetime.now()
        
        # Validate student can borrow more books
        cur = mysql.connection.cursor()
        cur.execute("""
            SELECT COUNT(*) as current_issues 
            FROM book_issues 
            WHERE student_id = %s AND status = 'Issued'
        """, (student_id,))
        issues = cur.fetchone()
        
        if issues['current_issues'] >= 5:  # Max 5 books per student
            return jsonify({'error': 'Student has reached maximum issued books limit'}), 400
            
        # Get book details
        cur.execute("""
            SELECT title, class, semester, quantity 
            FROM books 
            WHERE book_id = %s
        """, (book_id,))
        book = cur.fetchone()
        
        if not book or book['quantity'] <= 0:
            return jsonify({'error': 'Book not available for issuing'}), 400
            
        # Get student details and calculate current semester
        cur.execute("""
            SELECT class, admission_year FROM students WHERE student_id = %s
        """, (student_id,))
        student = cur.fetchone()
        
        if not student:
            return jsonify({'error': 'Student not found'}), 404
        
        # Calculate current semester based on admission year
        current_year = current_date.year
        current_month = current_date.month
        years_since_admission = current_year - student['admission_year']
        current_semester = years_since_admission * 2 + (1 if current_month <= 6 else 2)
        
        # Validate class/semester match
        if book['class'] != student['class'] or book['semester'] != current_semester:
            return jsonify({
                'error': f"Book is for {book['class']}-Sem{book['semester']}, student is in {student['class']}-Sem{current_semester}"
            }), 400
            
        # Create issue record
        issue_date = current_date.strftime('%Y-%m-%d')
        due_date = (current_date + timedelta(days=14)).strftime('%Y-%m-%d')
        
        cur.execute("""
            INSERT INTO book_issues 
            (student_id, book_id, issue_date, due_date, status)
            VALUES (%s, %s, %s, %s, 'Issued')
        """, (student_id, book_id, issue_date, due_date))
        
        # Update book quantity
        cur.execute("""
            UPDATE books SET quantity = quantity - 1 
            WHERE book_id = %s
        """, (book_id,))
        
        mysql.connection.commit()
        cur.close()
        
        return jsonify({
            'message': 'Book issued successfully',
            'due_date': due_date,
            'book_title': book['title'],
            'student_semester': current_semester  # Return calculated semester for reference
        })
        
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({'error': str(e)}), 500


# @app.route('/students/<int:student_id>/issued-books', methods=['GET'])
# def get_student_issued_books(student_id):
#     try:
#         cur = mysql.connection.cursor()
        
#         # Get student details first to verify student exists
#         cur.execute("SELECT name FROM students WHERE student_id = %s", (student_id,))
#         student = cur.fetchone()
        
#         if not student:
#             return jsonify({'error': 'Student not found'}), 404
        
#         # Get all issued books for this student
#         cur.execute("""
#             SELECT 
#                 bi.issue_id,
#                 b.book_id,
#                 b.title as book_title,
#                 b.author as book_author,
#                 b.class as book_class,
#                 bi.issue_date,
#                 bi.due_date,
#                 bi.return_date,
#                 bi.status,
#                 CASE 
#                     WHEN bi.status = 'Issued' AND bi.due_date < CURDATE() THEN 1
#                     ELSE 0
#                 END as overdue
#             FROM book_issues bi
#             JOIN books b ON bi.book_id = b.book_id
#             WHERE bi.student_id = %s
#             ORDER BY bi.issue_date DESC
#         """, (student_id,))
        
#         issued_books = cur.fetchall()
        
#         # Get current date (as date object, not datetime)
#         today = datetime.now().date()
        
#         # Calculate status for each book
#         for book in issued_books:
#             # Convert MySQL dates to Python date objects if they aren't already
#             issue_date = book['issue_date'].date() if isinstance(book['issue_date'], datetime) else book['issue_date']
#             due_date = book['due_date'].date() if isinstance(book['due_date'], datetime) else book['due_date']
#             return_date = book['return_date'].date() if book['return_date'] and isinstance(book['return_date'], datetime) else book['return_date']
            
#             book['issue_date'] = issue_date
#             book['due_date'] = due_date
#             book['return_date'] = return_date
            
#             # Calculate status
#             book['overdue'] = book['status'] == 'Issued' and due_date < today
#             book['status_display'] = 'Overdue' if book['overdue'] else book['status']
            
#             # Format dates as strings for JSON serialization
#             book['issue_date'] = issue_date.strftime('%Y-%m-%d') if issue_date else None
#             book['due_date'] = due_date.strftime('%Y-%m-%d') if due_date else None
#             book['return_date'] = return_date.strftime('%Y-%m-%d') if return_date else None
        
#         cur.close()
        
#         return jsonify({
#             'student_name': student['name'],
#             'student_id': student_id,
#             'issued_books': issued_books,
#             'total_issued': len(issued_books),
#             'currently_issued': len([b for b in issued_books if b['status'] == 'Issued']),
#             'overdue_books': len([b for b in issued_books if b['overdue']])
#         })
    
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500
    


@app.route('/students/<int:student_id>/issued-books', methods=['GET'])
def get_student_issued_books(student_id):
    try:
        cur = mysql.connection.cursor()
        
        # Verify student exists
        cur.execute("SELECT name FROM students WHERE student_id = %s", (student_id,))
        student = cur.fetchone()
        
        if not student:
            return jsonify({'error': 'Student not found'}), 404
        
        # Get issued books with book details
        cur.execute("""
            SELECT 
                bi.issue_id,
                b.book_id,
                b.title as book_title,
                b.author as book_author,
                b.class as book_class,
                b.subject as book_subject,
                DATE(bi.issue_date) as issue_date,
                DATE(bi.due_date) as due_date,
                bi.status
            FROM book_issues bi
            JOIN books b ON bi.book_id = b.book_id
            WHERE bi.student_id = %s
            ORDER BY bi.issue_date DESC
        """, (student_id,))
        
        issued_books = cur.fetchall()
        today = datetime.now().date()
        
        # Calculate additional fields
        for book in issued_books:
            # Convert string dates to date objects (if not already)
            issue_date = datetime.strptime(book['issue_date'], '%Y-%m-%d').date() if isinstance(book['issue_date'], str) else book['issue_date']
            due_date = datetime.strptime(book['due_date'], '%Y-%m-%d').date() if isinstance(book['due_date'], str) else book['due_date']
            
            # Calculate status display
            book['overdue'] = book['status'] == 'Issued' and due_date < today
            book['status_display'] = 'Overdue' if book['overdue'] else book['status']
            
            # Format dates consistently
            book['issue_date'] = issue_date.strftime('%Y-%m-%d')
            book['due_date'] = due_date.strftime('%Y-%m-%d')
        
        # Calculate summary counts
        total_issued = len(issued_books)
        currently_issued = len([b for b in issued_books if b['status'] == 'Issued'])
        overdue_books = len([b for b in issued_books if b.get('overdue', False)])
        
        cur.close()
        
        return jsonify({
            'success': True,
            'student_name': student['name'],
            'student_id': student_id,
            'issued_books': issued_books,
            'stats': {
                'total_issued': total_issued,
                'currently_issued': currently_issued,
                'overdue_books': overdue_books
            }
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
    



# Add these endpoints to your api.py

@app.route('/student/<int:student_id>/stats', methods=['GET'])
def get_student_stats(student_id):
    try:
        cur = mysql.connection.cursor()
        
        # 1. Get total books currently issued
        cur.execute("""
            SELECT COUNT(*) as issued_count 
            FROM book_issues 
            WHERE student_id = %s AND status = 'Issued'
        """, (student_id,))
        issued_count = cur.fetchone()['issued_count']
        
        # 2. Get pending returns (books not returned yet)
        # Same as issued_count in this simple system
        pending_returns = issued_count
        
        # 3. Calculate fines for overdue books
        today = datetime.now().strftime('%Y-%m-%d')
        cur.execute("""
            SELECT 
                SUM(DATEDIFF(%s, due_date) * 10) as total_fine
            FROM book_issues
            WHERE student_id = %s 
              AND status = 'Issued' 
              AND due_date < %s
        """, (today, student_id, today))
        fines_result = cur.fetchone()
        total_fine = fines_result['total_fine'] if fines_result['total_fine'] else 0
        
        cur.close()
        
        return jsonify({
            'success': True,
            'stats': {
                'issued_count': issued_count,
                'pending_returns': pending_returns,
                'total_fine': total_fine
            }
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/student/<int:student_id>/issued-books-student', methods=['GET'])
def get_student_issued_books_application(student_id):
    try:
        cur = mysql.connection.cursor()
        
        # Verify student exists
        cur.execute("SELECT name FROM students WHERE student_id = %s", (student_id,))
        student = cur.fetchone()
        
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'}), 404
        
        # Get issued books with book details
        cur.execute("""
            SELECT 
                bi.issue_id,
                b.book_id,
                b.title as book_title,
                b.author as book_author,
                DATE(bi.issue_date) as issue_date,
                DATE(bi.due_date) as due_date,
                bi.status,
                CASE 
                    WHEN bi.status = 'Issued' AND bi.due_date < CURDATE() THEN 1
                    ELSE 0
                END as is_overdue
            FROM book_issues bi
            JOIN books b ON bi.book_id = b.book_id
            WHERE bi.student_id = %s
            ORDER BY bi.issue_date DESC
        """, (student_id,))
        
        issued_books = cur.fetchall()
        
        # Convert to proper format
        for book in issued_books:
            book['overdue'] = bool(book['is_overdue'])
            book['coverImage'] = "https://via.placeholder.com/150"  # Placeholder image
        
        cur.close()
        
        return jsonify({
            'success': True,
            'issued_books': issued_books,
            'student_name': student['name']
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    

# @app.route('/students/<int:student_id>/issued-books', methods=['GET'])
# def get_student_issued_books(student_id):
#     try:
#         cur = mysql.connection.cursor()
        
#         # Get student details first to verify student exists
#         cur.execute("SELECT name FROM students WHERE student_id = %s", (student_id,))
#         student = cur.fetchone()
        
#         if not student:
#             return jsonify({'error': 'Student not found'}), 404
        
#         # Get all issued books for this student
#         cur.execute("""
#             SELECT 
#                 bi.issue_id,
#                 b.book_id,
#                 b.title as book_title,
#                 b.author as book_author,
#                 b.class as book_class,
#                 bi.issue_date,
#                 bi.due_date,
#                 bi.status,
#                 CASE 
#                     WHEN bi.status = 'Issued' AND bi.due_date < CURDATE() THEN 1
#                     ELSE 0
#                 END as overdue
#             FROM book_issues bi
#             JOIN books b ON bi.book_id = b.book_id
#             WHERE bi.student_id = %s
#             ORDER BY bi.issue_date DESC
#         """, (student_id,))
        
#         issued_books = cur.fetchall()
        
#         # Calculate status for each book
#         for book in issued_books:
#             book['overdue'] = bool(book['overdue'])
#             if book['status'] == 'Issued' and book['due_date'] < datetime.now().date():
#                 book['status_display'] = 'Overdue'
#             else:
#                 book['status_display'] = book['status']
        
#         cur.close()
        
#         return jsonify({
#             'student_name': student['name'],
#             'student_id': student_id,
#             'issued_books': issued_books,
#             'total_issued': len(issued_books),
#             'currently_issued': len([b for b in issued_books if b['status'] == 'Issued']),
#             'overdue_books': len([b for b in issued_books if b['overdue']])
#         })
    
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500
    

@app.route('/send-notification', methods=['POST'])
def send_notification():
    try:
        data = request.get_json()
        token = data['fcm_token']
        title = data.get('title', 'New Notification')
        body = data.get('body', 'You have a new message')
        
        # Create notification message
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            token=token
        )
        
        # Send message
        response = messaging.send(message)
        
        return jsonify({
            "success": True,
            "message_id": response,
            "message": "Notification sent successfully"
        })
    
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    



# API 2: Issue book
@app.route('/issue', methods=['POST'])
def old_issue_book():
    try:
        data = request.get_json()
        if not data.get('book_id') or not data.get('student_id'):
            return jsonify({"success": False, "error": "Missing book_id or student_id"}), 400

        due_date = (datetime.now() + timedelta(days=15)).strftime('%Y-%m-%d')
        cur = mysql.connection.cursor()
        cur.execute(
            "INSERT INTO transactions (book_id, student_id, issue_date, due_date) VALUES (%s, %s, %s, %s)",
            (data['book_id'], data['student_id'], datetime.now().strftime('%Y-%m-%d'), due_date)
        )
        mysql.connection.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    

# API 3: Return book + calculate fine
# Return book + fine
@app.route('/return', methods=['POST'])
def old_return_book():
    try:
        data = request.get_json()
        if not data.get('txn_id'):
            return jsonify({"success": False, "error": "Missing txn_id"}), 400

        cur = mysql.connection.cursor()
        cur.execute("SELECT due_date FROM transactions WHERE txn_id = %s", (data['txn_id'],))
        txn = cur.fetchone()
        if not txn:
            return jsonify({"success": False, "error": "Transaction not found"}), 404

        due_date = txn['due_date']
        return_date = datetime.now().date()
        days_late = (return_date - due_date).days
        fine = max(0, days_late) * 10  # ₹10/day fine

        cur.execute(
            "UPDATE transactions SET return_date = %s, fine = %s WHERE txn_id = %s",
            (return_date, fine, data['txn_id'])
        )
        mysql.connection.commit()
        return jsonify({"success": True, "fine": fine})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True,host='0.0.0.0')  # http://localhost:5000

