from database import get_db_connection
from werkzeug.security import generate_password_hash
from datetime import date, datetime
import os
from functools import wraps
import re
from decimal import Decimal, InvalidOperation



from flask import (
    Flask, render_template, request,
    redirect, url_for, session, flash
)
from werkzeug.security import check_password_hash
from database import get_db_connection


app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change-this-to-a-long-random-secret-key"
)


# --------------------------------------------------
# DATABASE HELPER
# --------------------------------------------------

def fetch_one(query, values=()):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(query, values)
        return cursor.fetchone()
    finally:
        cursor.close()
        db.close()


def fetch_all(query, values=()):
    db = get_db_connection()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute(query, values)
        return cursor.fetchall()
    finally:
        cursor.close()
        db.close()


# --------------------------------------------------
# AUTHENTICATION AND ACCESS CONTROL
# --------------------------------------------------

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def role_required(*allowed_roles):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            if session.get("role") not in allowed_roles:
                flash("You do not have permission to access this module.", "danger")

                # Send Employee users to their own workspace, never through
                # the HR dashboard. This prevents role-based redirect loops.
                if session.get("role") == "Employee":
                    return redirect(url_for("employee_portal"))

                return redirect(url_for("dashboard"))

            return function(*args, **kwargs)

        return wrapper

    return decorator


# --------------------------------------------------
# HOME
# --------------------------------------------------


@app.route("/")
def home():

    if "user_id" in session:

        if session.get("role") == "Employee":
            return redirect(url_for("employee_portal"))

        return redirect(url_for("dashboard"))

    return render_template("home.html")


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        try:
            user = fetch_one(
                """
                SELECT user_id, username, password_hash, role, employee_id
                FROM users
                WHERE username = %s
                """,
                (username,)
            )

            if not user or not check_password_hash(user["password_hash"], password):
                # Clear any previous account session so a failed login cannot
                # leave the browser signed in as an older user.
                session.clear()
                flash("Invalid username or password.", "danger")
                return render_template("login.html"), 401

            session.clear()
            session["user_id"] = user["user_id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["employee_id"] = user["employee_id"]

            flash("Welcome to your NEXORA workspace.", "success")

            if user["role"] == "Employee":
                return redirect(url_for("employee_portal"))

            return redirect(url_for("dashboard"))

        except Exception:
            app.logger.exception("Login error")
            session.clear()
            flash("Unable to connect to the application database.", "danger")
            return render_template("login.html"), 500

    return render_template("login.html")


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------


@app.route("/logout")
@login_required
def logout():

    session.clear()

    return redirect(url_for("home"))



# --------------------------------------------------
# ENTERPRISE DASHBOARD
# --------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") == "Employee":
        return redirect(url_for("employee_portal"))

    try:
        metrics = fetch_one(
            """
            SELECT
                COUNT(*) AS total_employees,
                SUM(
                    CASE
                        WHEN employment_status = 'Active'
                        THEN 1 ELSE 0
                    END
                ) AS active_employees,
                SUM(
                    CASE
                        WHEN employment_status = 'Inactive'
                        THEN 1 ELSE 0
                    END
                ) AS inactive_employees
            FROM employees
            """
        )

        department_count = fetch_one(
            "SELECT COUNT(*) AS total_departments FROM departments"
        )

        recent_employees = fetch_all(
            """
            SELECT
                e.employee_id,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.designation,
                e.employment_status,
                d.department_name
            FROM employees e
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            ORDER BY e.employee_id DESC
            LIMIT 5
            """
        )

        return render_template(
            "dashboard.html",
            metrics=metrics,
            department_count=department_count,
            recent_employees=recent_employees
        )

    except Exception:
        app.logger.exception("Dashboard error")
        flash("Unable to load dashboard information.", "danger")

        return render_template(
            "dashboard.html",
            metrics={
                "total_employees": 0,
                "active_employees": 0,
                "inactive_employees": 0
            },
            department_count={"total_departments": 0},
            recent_employees=[]
        )


# --------------------------------------------------
# EMPLOYEE DIRECTORY
# --------------------------------------------------

@app.route("/hr/employees")
@login_required
@role_required("HR Admin", "Manager")
def employees():

    employee_list = fetch_all(
        """
        SELECT
            e.employee_id,
            e.employee_code,
            e.first_name,
            e.last_name,
            e.email,
            e.designation,
            e.joining_date,
            e.employment_status,
            d.department_name
        FROM employees e
        LEFT JOIN departments d
            ON e.department_id = d.department_id
        ORDER BY e.employee_id DESC
        """
    )

    return render_template(
        "employees.html",
        employees=employee_list
    )


# --------------------------------------------------
# ADD EMPLOYEE
# --------------------------------------------------

@app.route("/hr/employees/add", methods=["GET", "POST"])
@login_required
@role_required("HR Admin")
def add_employee():

    departments = fetch_all(
        "SELECT * FROM departments ORDER BY department_name"
    )

    if request.method == "POST":

        employee_data = (
            request.form.get("employee_code", "").strip(),
            request.form.get("first_name", "").strip(),
            request.form.get("last_name", "").strip(),
            request.form.get("email", "").strip(),
            request.form.get("department_id") or None,
            request.form.get("designation", "").strip(),
            request.form.get("joining_date") or None
        )

        if not employee_data[0] or not employee_data[1] or not employee_data[3]:
            flash("Employee code, first name and email are required.", "danger")

            return render_template(
                "employee_form.html",
                departments=departments,
                employee=None
            )

        db = get_db_connection()
        cursor = db.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO employees (
                    employee_code,
                    first_name,
                    last_name,
                    email,
                    department_id,
                    designation,
                    joining_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                employee_data
            )

            db.commit()

            flash("Employee successfully registered.", "success")

            return redirect(url_for("employees"))

        except Exception:
            db.rollback()
            app.logger.exception("Employee registration error")

            flash(
                "Unable to register employee. Check for duplicate employee code or email.",
                "danger"
            )

        finally:
            cursor.close()
            db.close()

    return render_template(
        "employee_form.html",
        departments=departments,
        employee=None
    )


# --------------------------------------------------
# EDIT EMPLOYEE
# --------------------------------------------------

@app.route(
    "/hr/employees/<int:employee_id>/edit",
    methods=["GET", "POST"]
)
@login_required
@role_required("HR Admin")
def edit_employee(employee_id):

    employee = fetch_one(
        "SELECT * FROM employees WHERE employee_id = %s",
        (employee_id,)
    )

    if not employee:
        return "Employee not found", 404

    departments = fetch_all(
        "SELECT * FROM departments ORDER BY department_name"
    )

    if request.method == "POST":

        db = get_db_connection()
        cursor = db.cursor()

        try:
            cursor.execute(
                """
                UPDATE employees
                SET
                    first_name = %s,
                    last_name = %s,
                    email = %s,
                    department_id = %s,
                    designation = %s,
                    joining_date = %s,
                    employment_status = %s
                WHERE employee_id = %s
                """,
                (
                    request.form.get("first_name", "").strip(),
                    request.form.get("last_name", "").strip(),
                    request.form.get("email", "").strip(),
                    request.form.get("department_id") or None,
                    request.form.get("designation", "").strip(),
                    request.form.get("joining_date") or None,
                    request.form.get("employment_status", "Active"),
                    employee_id
                )
            )

            db.commit()

            flash("Employee information updated successfully.", "success")

            return redirect(url_for("employees"))

        except Exception:
            db.rollback()
            app.logger.exception("Employee update error")

            flash("Unable to update employee information.", "danger")

        finally:
            cursor.close()
            db.close()

    return render_template(
        "employee_form.html",
        departments=departments,
        employee=employee
    )


# --------------------------------------------------
# DEACTIVATE EMPLOYEE
# --------------------------------------------------

@app.route(
    "/hr/employees/<int:employee_id>/deactivate",
    methods=["POST"]
)
@login_required
@role_required("HR Admin")
def deactivate_employee(employee_id):

    db = get_db_connection()
    cursor = db.cursor()

    try:
        cursor.execute(
            """
            UPDATE employees
            SET employment_status = 'Inactive'
            WHERE employee_id = %s
            """,
            (employee_id,)
        )

        db.commit()

        flash("Employee deactivated successfully.", "success")

    except Exception:
        db.rollback()
        app.logger.exception("Employee deactivation error")

        flash("Unable to deactivate employee.", "danger")

    finally:
        cursor.close()
        db.close()

    return redirect(url_for("employees"))


# --------------------------------------------------
# APPLICATION STATUS
# --------------------------------------------------

@app.route("/health")
def health():

    try:
        fetch_one("SELECT 1")

        return {
            "application": "HR Employee Management System",
            "database": "Connected",
            "status": "Running"
        }, 200

    except Exception:
        return {
            "database": "Disconnected",
            "status": "Error"
        }, 500




@app.route("/hr/leave")
@login_required
@role_required("HR Admin", "Manager")
def leave_management():

    query = """
        SELECT
            lr.leave_id,
            lr.employee_id,
            lr.leave_type,
            lr.start_date,
            lr.end_date,
            lr.reason,
            lr.status,
            lr.applied_at,
            e.employee_code,
            e.first_name,
            e.last_name,
            e.designation,
            d.department_name
        FROM leave_requests lr
        JOIN employees e
            ON lr.employee_id = e.employee_id
        LEFT JOIN departments d
            ON e.department_id = d.department_id
        ORDER BY
            CASE WHEN lr.status = 'Pending' THEN 0 ELSE 1 END,
            lr.applied_at DESC
    """

    leave_requests = fetch_all(query)

    employees = fetch_all("""
        SELECT employee_id, employee_code, first_name, last_name
        FROM employees
        WHERE employment_status = 'Active'
        ORDER BY first_name
    """)

    pending_count = sum(
        1 for item in leave_requests
        if item["status"] == "Pending"
    )

    approved_count = sum(
        1 for item in leave_requests
        if item["status"] == "Approved"
    )

    rejected_count = sum(
        1 for item in leave_requests
        if item["status"] == "Rejected"
    )

    return render_template(
        "leave.html",
        leave_requests=leave_requests,
        employees=employees,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count
    )


@app.route("/hr/leave/create", methods=["POST"])
@login_required
@role_required("HR Admin", "Manager")
def create_leave_request():

    from datetime import datetime

    employee_id = request.form.get("employee_id")
    leave_type = request.form.get("leave_type")
    start_date = request.form.get("start_date")
    end_date = request.form.get("end_date")
    reason = request.form.get("reason", "").strip()

    if not all([employee_id, leave_type, start_date, end_date]):
        flash("Please complete all required leave details.", "danger")
        return redirect(url_for("leave_management"))

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()

        if end < start:
            flash("End date cannot be before start date.", "danger")
            return redirect(url_for("leave_management"))

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO leave_requests
            (employee_id, leave_type, start_date, end_date, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (employee_id, leave_type, start, end, reason))

        conn.commit()

        flash("Leave request created successfully.", "success")

    except ValueError:
        flash("Please enter valid leave dates.", "danger")

    except Exception as error:
        if "conn" in locals():
            conn.rollback()
        print("Leave creation error:", error)
        flash("Unable to create leave request. Check the server log.", "danger")

    finally:
        if "cursor" in locals():
            cursor.close()
        if "conn" in locals():
            conn.close()

    return redirect(url_for("leave_management"))


@app.route("/hr/leave/<int:leave_id>/review", methods=["POST"])
@login_required
@role_required("HR Admin", "Manager")
def review_leave(leave_id):

    decision = request.form.get("decision")

    if decision not in ("Approved", "Rejected"):
        flash("Invalid leave decision.", "danger")
        return redirect(url_for("leave_management"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE leave_requests
            SET status = %s,
                reviewed_by = %s,
                reviewed_at = NOW()
            WHERE leave_id = %s
              AND status = 'Pending'
        """, (
            decision,
            session["user_id"],
            leave_id
        ))

        conn.commit()

        if cursor.rowcount == 1:
            flash(
                f"Leave request {decision.lower()} successfully.",
                "success"
            )
        else:
            flash("Request not found or already reviewed.", "warning")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Leave review error:", error)
        flash("Unable to update leave request.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("leave_management"))



@app.route("/hr/attendance")
@login_required
@role_required("HR Admin", "Manager")
def attendance_management():

    selected_date = request.args.get("date") or date.today().isoformat()

    try:
        datetime.strptime(selected_date, "%Y-%m-%d")
    except ValueError:
        selected_date = date.today().isoformat()

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                e.employee_id,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.designation,
                d.department_name,
                a.attendance_status,
                a.check_in,
                a.check_out,
                a.remarks
            FROM employees e
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            LEFT JOIN attendance_records a
                ON e.employee_id = a.employee_id
                AND a.attendance_date = %s
            WHERE e.employment_status = 'Active'
            ORDER BY e.first_name, e.last_name
        """, (selected_date,))

        attendance_records = cursor.fetchall()

        counts = {
            "total": len(attendance_records),
            "present": 0,
            "absent": 0,
            "half_day": 0,
            "on_leave": 0,
            "unmarked": 0
        }

        for record in attendance_records:
            status = record["attendance_status"]

            if status == "Present":
                counts["present"] += 1
            elif status == "Absent":
                counts["absent"] += 1
            elif status == "Half Day":
                counts["half_day"] += 1
            elif status == "On Leave":
                counts["on_leave"] += 1
            else:
                counts["unmarked"] += 1

            # Convert database TIME values into HTML time-input format.
            for field in ("check_in", "check_out"):
                value = record[field]

                if value is None:
                    record[field] = ""
                elif hasattr(value, "total_seconds"):
                    seconds = int(value.total_seconds())
                    record[field] = (
                        f"{seconds // 3600:02d}:"
                        f"{(seconds % 3600) // 60:02d}"
                    )
                elif hasattr(value, "strftime"):
                    record[field] = value.strftime("%H:%M")
                else:
                    record[field] = str(value)[:5]

        return render_template(
            "attendance.html",
            attendance_records=attendance_records,
            counts=counts,
            selected_date=selected_date
        )

    except Exception as error:
        print("Attendance loading error:", error)
        flash("Unable to load attendance records.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route(
    "/hr/attendance/<int:employee_id>/mark",
    methods=["POST"]
)
@login_required
@role_required("HR Admin", "Manager")
def mark_attendance(employee_id):

    attendance_date = request.form.get("attendance_date")
    attendance_status = request.form.get("attendance_status")
    check_in = request.form.get("check_in") or None
    check_out = request.form.get("check_out") or None
    remarks = request.form.get("remarks", "").strip() or None

    allowed_statuses = (
        "Present", "Absent", "Half Day", "On Leave"
    )

    try:
        datetime.strptime(attendance_date, "%Y-%m-%d")

        if attendance_status not in allowed_statuses:
            raise ValueError("Invalid attendance status")

        if attendance_status in ("Absent", "On Leave"):
            check_in = None
            check_out = None

        for time_value in (check_in, check_out):
            if time_value:
                datetime.strptime(time_value, "%H:%M")

        if check_in and check_out and check_out < check_in:
            raise ValueError("Check-out cannot be earlier than check-in")

    except (ValueError, TypeError):
        flash("Please check the attendance date, status and times.", "danger")
        return redirect(
            url_for("attendance_management", date=attendance_date)
        )

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO attendance_records
            (
                employee_id,
                attendance_date,
                check_in,
                check_out,
                attendance_status,
                remarks,
                marked_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)

            ON DUPLICATE KEY UPDATE
                check_in = VALUES(check_in),
                check_out = VALUES(check_out),
                attendance_status = VALUES(attendance_status),
                remarks = VALUES(remarks),
                marked_by = VALUES(marked_by)
        """, (
            employee_id,
            attendance_date,
            check_in,
            check_out,
            attendance_status,
            remarks,
            session["user_id"]
        ))

        conn.commit()
        flash("Attendance saved successfully.", "success")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Attendance saving error:", error)
        flash("Unable to save attendance. Check the server log.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(
        url_for("attendance_management", date=attendance_date)
    )
    
    
@app.route("/hr/payroll")
@login_required
@role_required("HR Admin")
def payroll_management():

    from datetime import date, datetime
    from decimal import Decimal

    selected_period = request.args.get(
        "period", date.today().strftime("%Y-%m")
    )

    try:
        period_date = datetime.strptime(
            selected_period, "%Y-%m"
        ).date().replace(day=1)
        selected_period = period_date.strftime("%Y-%m")
    except ValueError:
        period_date = date.today().replace(day=1)
        selected_period = period_date.strftime("%Y-%m")

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                p.payroll_id,
                p.employee_id,
                p.pay_period,
                p.basic_salary,
                p.allowances,
                p.deductions,
                p.net_salary,
                p.payment_status,
                p.payment_date,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.designation,
                d.department_name
            FROM payroll_records p
            JOIN employees e
                ON p.employee_id = e.employee_id
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            WHERE p.pay_period = %s
            ORDER BY e.first_name, e.last_name
        """, (period_date,))

        payrolls = cursor.fetchall()

        cursor.execute("""
            SELECT
                employee_id,
                employee_code,
                first_name,
                last_name,
                designation
            FROM employees
            WHERE employment_status = 'Active'
            ORDER BY first_name, last_name
        """)

        employees = cursor.fetchall()

        summary = {
            "total_records": len(payrolls),
            "total_net_salary": sum(
                (p["net_salary"] or Decimal("0.00") for p in payrolls),
                Decimal("0.00")
            ),
            "processed": sum(
                1 for p in payrolls
                if p["payment_status"] == "Processed"
            ),
            "draft": sum(
                1 for p in payrolls
                if p["payment_status"] == "Draft"
            ),
            "paid": sum(
                1 for p in payrolls
                if p["payment_status"] == "Paid"
            )
        }

        return render_template(
            "payroll.html",
            payrolls=payrolls,
            employees=employees,
            summary=summary,
            selected_period=selected_period
        )

    except Exception as error:
        print("Payroll loading error:", error)
        flash("Unable to load payroll records.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/hr/payroll/create", methods=["POST"])
@login_required
@role_required("HR Admin")
def create_payroll():

    from datetime import datetime
    from decimal import Decimal, InvalidOperation

    conn = None
    cursor = None

    selected_period = request.form.get("pay_period", "")

    try:
        employee_id = int(request.form.get("employee_id", ""))
        period = datetime.strptime(
            selected_period, "%Y-%m"
        ).date().replace(day=1)

        basic = Decimal(request.form.get("basic_salary", "0"))
        allowances = Decimal(request.form.get("allowances", "0") or "0")
        deductions = Decimal(request.form.get("deductions", "0") or "0")

        amounts = (basic, allowances, deductions)

        if any(not amount.is_finite() or amount < 0 for amount in amounts):
            raise ValueError("Salary amounts cannot be negative.")

        if basic <= 0:
            raise ValueError("Basic salary must be greater than zero.")

        net_salary = basic + allowances - deductions

        if net_salary < 0:
            raise ValueError("Deductions cannot exceed gross salary.")

        payment_status = request.form.get("payment_status", "Draft")

        if payment_status not in ("Draft", "Processed"):
            raise ValueError("Invalid initial payroll status.")

        processed_by = (
            session["user_id"] if payment_status == "Processed" else None
        )

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO payroll_records (
                employee_id,
                pay_period,
                basic_salary,
                allowances,
                deductions,
                net_salary,
                payment_status,
                processed_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            employee_id,
            period,
            basic,
            allowances,
            deductions,
            net_salary,
            payment_status,
            processed_by
        ))

        conn.commit()
        flash("Payroll record created successfully.", "success")

    except (ValueError, InvalidOperation) as error:
        flash(str(error), "danger")

    except Exception as error:
        if conn:
            conn.rollback()

        print("Payroll creation error:", error)
        flash(
            "Unable to create payroll. A record for this employee and month "
            "may already exist.",
            "danger"
        )

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(
        url_for("payroll_management", period=selected_period)
    )
    
    
@app.route("/hr/reports")
@login_required
@role_required("HR Admin", "Manager")
def hr_reports():

    from datetime import date, datetime

    selected_period = request.args.get(
        "period", date.today().strftime("%Y-%m")
    )

    try:
        period_start = datetime.strptime(
            selected_period, "%Y-%m"
        ).date().replace(day=1)
        selected_period = period_start.strftime("%Y-%m")
    except ValueError:
        period_start = date.today().replace(day=1)
        selected_period = period_start.strftime("%Y-%m")

    if period_start.month == 12:
        period_end = date(period_start.year + 1, 1, 1)
    else:
        period_end = date(
            period_start.year, period_start.month + 1, 1
        )

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Workforce by employment status
        cursor.execute("""
            SELECT
                employment_status,
                COUNT(*) AS total
            FROM employees
            GROUP BY employment_status
        """)
        employee_rows = cursor.fetchall()

        employee_status = {
            row["employment_status"]: row["total"]
            for row in employee_rows
        }

        total_employees = sum(employee_status.values())
        active_employees = employee_status.get("Active", 0)
        inactive_employees = employee_status.get("Inactive", 0)

        # Department headcount
        cursor.execute("""
            SELECT
                d.department_name,
                COUNT(e.employee_id) AS employee_count
            FROM departments d
            LEFT JOIN employees e
                ON d.department_id = e.department_id
            GROUP BY d.department_id, d.department_name
            ORDER BY employee_count DESC, d.department_name
        """)
        department_stats = cursor.fetchall()

        max_department_count = max(
            (row["employee_count"] for row in department_stats),
            default=0
        )

        for row in department_stats:
            row["percentage"] = round(
                row["employee_count"] / max_department_count * 100
            ) if max_department_count else 0

        # Leave activity for selected month
        cursor.execute("""
            SELECT status, COUNT(*) AS total
            FROM leave_requests
            WHERE applied_at >= %s
              AND applied_at < %s
            GROUP BY status
        """, (period_start, period_end))

        leave_rows = cursor.fetchall()
        leave_status = {
            row["status"]: row["total"] for row in leave_rows
        }

        # Attendance for selected month
        cursor.execute("""
            SELECT attendance_status, COUNT(*) AS total
            FROM attendance_records
            WHERE attendance_date >= %s
              AND attendance_date < %s
            GROUP BY attendance_status
        """, (period_start, period_end))

        attendance_rows = cursor.fetchall()
        attendance_status = {
            row["attendance_status"]: row["total"]
            for row in attendance_rows
        }

        # Payroll for selected month
        cursor.execute("""
            SELECT
                COUNT(*) AS record_count,
                COALESCE(SUM(basic_salary), 0) AS basic_total,
                COALESCE(SUM(allowances), 0) AS allowances_total,
                COALESCE(SUM(deductions), 0) AS deductions_total,
                COALESCE(SUM(net_salary), 0) AS net_total
            FROM payroll_records
            WHERE pay_period = %s
        """, (period_start,))

        payroll_summary = cursor.fetchone()

        return render_template(
            "reports.html",
            selected_period=selected_period,
            total_employees=total_employees,
            active_employees=active_employees,
            inactive_employees=inactive_employees,
            department_stats=department_stats,
            pending_leaves=leave_status.get("Pending", 0),
            approved_leaves=leave_status.get("Approved", 0),
            rejected_leaves=leave_status.get("Rejected", 0),
            total_leaves=sum(leave_status.values()),
            present_count=attendance_status.get("Present", 0),
            absent_count=attendance_status.get("Absent", 0),
            half_day_count=attendance_status.get("Half Day", 0),
            on_leave_count=attendance_status.get("On Leave", 0),
            payroll_summary=payroll_summary
        )

    except Exception as error:
        print("Reports loading error:", error)
        flash("Unable to generate reports. Check the server log.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()




@app.route("/hr/organization")
@login_required
@role_required("HR Admin", "Manager")
def organization_structure():

    departments = fetch_all("""
        SELECT
            d.department_id,
            d.department_name,
            COUNT(e.employee_id) AS total_employees,
            COALESCE(
                SUM(
                    CASE
                        WHEN e.employment_status = 'Active' THEN 1
                        ELSE 0
                    END
                ), 0
            ) AS active_employees
        FROM departments d
        LEFT JOIN employees e
            ON d.department_id = e.department_id
        GROUP BY d.department_id, d.department_name
        ORDER BY d.department_name
    """)

    employees = fetch_all("""
        SELECT
            e.employee_id,
            e.employee_code,
            e.first_name,
            e.last_name,
            e.designation,
            e.employment_status,
            d.department_name
        FROM employees e
        LEFT JOIN departments d
            ON e.department_id = d.department_id
        ORDER BY d.department_name, e.first_name
    """)

    total_departments = len(departments)
    total_employees = sum(
        department["total_employees"] or 0
        for department in departments
    )
    active_employees = sum(
        department["active_employees"] or 0
        for department in departments
    )

    largest_department = max(
        (department["total_employees"] or 0 for department in departments),
        default=0
    )

    for department in departments:
        count = department["total_employees"] or 0
        department["workforce_percentage"] = (
            round(count / largest_department * 100)
            if largest_department else 0
        )

    return render_template(
        "organization.html",
        departments=departments,
        employees=employees,
        total_departments=total_departments,
        total_employees=total_employees,
        active_employees=active_employees
    )
    
@app.route("/hr/recruitment")
@login_required
@role_required("HR Admin", "Manager")
def recruitment_management():

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                j.job_id,
                j.job_title,
                j.employment_type,
                j.work_location,
                j.number_of_openings,
                j.minimum_salary,
                j.maximum_salary,
                j.job_status,
                j.created_at,
                d.department_name,
                COUNT(a.application_id) AS applicant_count
            FROM job_openings j
            LEFT JOIN departments d
                ON j.department_id = d.department_id
            LEFT JOIN job_applications a
                ON j.job_id = a.job_id
            GROUP BY
                j.job_id, j.job_title, j.employment_type,
                j.work_location, j.number_of_openings,
                j.minimum_salary, j.maximum_salary,
                j.job_status, j.created_at, d.department_name
            ORDER BY j.created_at DESC
        """)
        jobs = cursor.fetchall()

        cursor.execute("""
            SELECT
                a.application_id,
                a.job_id,
                a.candidate_name,
                a.candidate_email,
                a.candidate_phone,
                a.resume_url,
                a.application_source,
                a.application_status,
                a.interview_date,
                a.recruiter_notes,
                a.applied_at,
                j.job_title
            FROM job_applications a
            JOIN job_openings j
                ON a.job_id = j.job_id
            ORDER BY a.applied_at DESC
        """)
        applications = cursor.fetchall()

        cursor.execute("""
            SELECT department_id, department_name
            FROM departments
            ORDER BY department_name
        """)
        departments = cursor.fetchall()

        cursor.execute("""
            SELECT job_id, job_title
            FROM job_openings
            WHERE job_status = 'Open'
            ORDER BY job_title
        """)
        open_jobs = cursor.fetchall()

        metrics = {
            "total_jobs": len(jobs),
            "open_jobs": sum(1 for j in jobs if j["job_status"] == "Open"),
            "total_applicants": len(applications),
            "interviews": sum(
                1 for a in applications
                if a["application_status"] == "Interview"
            ),
            "hired": sum(
                1 for a in applications
                if a["application_status"] == "Hired"
            )
        }

        return render_template(
            "recruitment.html",
            jobs=jobs,
            applications=applications,
            departments=departments,
            open_jobs=open_jobs,
            metrics=metrics
        )

    except Exception as error:
        print("Recruitment loading error:", error)
        flash("Unable to load recruitment records.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/hr/recruitment/jobs/create", methods=["POST"])
@login_required
@role_required("HR Admin", "Manager")
def create_job_opening():

    conn = None
    cursor = None

    try:
        job_title = request.form.get("job_title", "").strip()
        department_value = request.form.get("department_id", "").strip()
        employment_type = request.form.get("employment_type", "Full Time")
        work_location = request.form.get("work_location", "").strip()
        openings = int(request.form.get("number_of_openings", "1"))
        minimum_value = request.form.get("minimum_salary", "").strip()
        maximum_value = request.form.get("maximum_salary", "").strip()
        description = request.form.get("job_description", "").strip()
        job_status = request.form.get("job_status", "Draft")

        allowed_types = ("Full Time", "Part Time", "Contract", "Internship")
        allowed_statuses = ("Draft", "Open", "Closed")

        if not job_title:
            raise ValueError("Job title is required.")

        if employment_type not in allowed_types:
            raise ValueError("Invalid employment type.")

        if job_status not in allowed_statuses:
            raise ValueError("Invalid job status.")

        if openings < 1:
            raise ValueError("Number of openings must be at least one.")

        department_id = int(department_value) if department_value else None

        minimum_salary = (
            Decimal(minimum_value) if minimum_value else None
        )
        maximum_salary = (
            Decimal(maximum_value) if maximum_value else None
        )

        for amount in (minimum_salary, maximum_salary):
            if amount is not None and (
                not amount.is_finite() or amount < 0
            ):
                raise ValueError("Salary values must be valid and non-negative.")

        if (
            minimum_salary is not None
            and maximum_salary is not None
            and maximum_salary < minimum_salary
        ):
            raise ValueError("Maximum salary cannot be below minimum salary.")

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO job_openings (
                job_title,
                department_id,
                employment_type,
                work_location,
                number_of_openings,
                minimum_salary,
                maximum_salary,
                job_description,
                job_status,
                created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            job_title,
            department_id,
            employment_type,
            work_location or None,
            openings,
            minimum_salary,
            maximum_salary,
            description or None,
            job_status,
            session["user_id"]
        ))

        conn.commit()
        flash("Job opening created successfully.", "success")

    except (ValueError, InvalidOperation) as error:
        flash(str(error), "danger")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Job creation error:", error)
        flash("Unable to create job opening. Check the server log.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("recruitment_management"))


@app.route("/hr/recruitment/applications/create", methods=["POST"])
@login_required
@role_required("HR Admin", "Manager")
def create_job_application():

    conn = None
    cursor = None

    try:
        job_id = int(request.form.get("job_id", ""))
        candidate_name = request.form.get("candidate_name", "").strip()
        candidate_email = request.form.get("candidate_email", "").strip()
        candidate_phone = request.form.get("candidate_phone", "").strip()
        resume_url = request.form.get("resume_url", "").strip()
        application_source = request.form.get("application_source", "").strip()
        recruiter_notes = request.form.get("recruiter_notes", "").strip()

        if not candidate_name or not candidate_email:
            raise ValueError("Candidate name and email are required.")

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate_email):
            raise ValueError("Enter a valid candidate email address.")

        if resume_url and not resume_url.lower().startswith(("https://", "http://")):
            raise ValueError("Resume link must start with http:// or https://.")

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT job_id
            FROM job_openings
            WHERE job_id = %s AND job_status = 'Open'
        """, (job_id,))

        if not cursor.fetchone():
            raise ValueError("Selected job is not currently open.")

        cursor.execute("""
            INSERT INTO job_applications (
                job_id,
                candidate_name,
                candidate_email,
                candidate_phone,
                resume_url,
                application_source,
                recruiter_notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            job_id,
            candidate_name,
            candidate_email,
            candidate_phone or None,
            resume_url or None,
            application_source or None,
            recruiter_notes or None
        ))

        conn.commit()
        flash("Candidate registered successfully.", "success")

    except (ValueError, InvalidOperation) as error:
        flash(str(error), "danger")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Candidate registration error:", error)
        flash("Unable to register candidate. Check the server log.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("recruitment_management"))


@app.route(
    "/hr/recruitment/applications/<int:application_id>/status",
    methods=["POST"]
)
@login_required
@role_required("HR Admin", "Manager")
def update_application_status(application_id):

    from datetime import datetime

    allowed_statuses = (
        "Applied",
        "Screening",
        "Interview",
        "Offered",
        "Hired",
        "Rejected"
    )

    new_status = request.form.get("application_status")
    interview_value = request.form.get("interview_date", "").strip()

    if new_status not in allowed_statuses:
        flash("Invalid application status.", "danger")
        return redirect(url_for("recruitment_management"))

    conn = None
    cursor = None
    interview_date = None

    try:
        if interview_value:
            interview_date = datetime.strptime(
                interview_value, "%Y-%m-%dT%H:%M"
            )

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE job_applications
            SET application_status = %s,
                interview_date = COALESCE(%s, interview_date)
            WHERE application_id = %s
        """, (
            new_status,
            interview_date,
            application_id
        ))

        if cursor.rowcount > 0:
            conn.commit()
            flash("Candidate pipeline updated successfully.", "success")
        else:
            flash("Application not found or no changes made.", "warning")

    except ValueError:
        if conn:
            conn.rollback()
        flash("Enter a valid interview date and time.", "danger")

    except Exception as error:
        if conn:
            conn.rollback()

        print("Application status update error:", error)
        flash("Unable to update candidate status.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("recruitment_management"))

    


@app.route("/hr/onboarding")
@login_required
@role_required("HR Admin", "Manager")
def onboarding_management():

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                o.onboarding_id,
                o.employee_id,
                o.onboarding_status,
                o.start_date,
                o.completion_date,
                o.notes,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.designation,
                d.department_name,
                COUNT(t.task_id) AS total_tasks,
                COALESCE(
                    SUM(
                        CASE
                            WHEN t.task_status = 'Completed' THEN 1
                            ELSE 0
                        END
                    ), 0
                ) AS completed_tasks
            FROM onboarding_records o
            JOIN employees e
                ON o.employee_id = e.employee_id
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            LEFT JOIN onboarding_tasks t
                ON o.onboarding_id = t.onboarding_id
            GROUP BY
                o.onboarding_id,
                o.employee_id,
                o.onboarding_status,
                o.start_date,
                o.completion_date,
                o.notes,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.designation,
                d.department_name
            ORDER BY o.created_at DESC
        """)
        onboardings = cursor.fetchall()

        cursor.execute("""
            SELECT
                t.task_id,
                t.onboarding_id,
                t.task_title,
                t.task_category,
                t.task_status,
                t.due_date,
                t.completed_at,
                t.task_notes
            FROM onboarding_tasks t
            ORDER BY t.due_date IS NULL, t.due_date, t.task_id
        """)
        tasks = cursor.fetchall()

        tasks_by_onboarding = {}

        for task in tasks:
            onboarding_id = task["onboarding_id"]
            tasks_by_onboarding.setdefault(onboarding_id, []).append(task)

        cursor.execute("""
            SELECT
                e.employee_id,
                e.employee_code,
                e.first_name,
                e.last_name
            FROM employees e
            LEFT JOIN onboarding_records o
                ON e.employee_id = o.employee_id
            WHERE e.employment_status = 'Active'
              AND o.onboarding_id IS NULL
            ORDER BY e.first_name
        """)
        available_employees = cursor.fetchall()

        summary = {
            "total": len(onboardings),
            "not_started": sum(
                1 for o in onboardings
                if o["onboarding_status"] == "Not Started"
            ),
            "in_progress": sum(
                1 for o in onboardings
                if o["onboarding_status"] == "In Progress"
            ),
            "completed": sum(
                1 for o in onboardings
                if o["onboarding_status"] == "Completed"
            ),
            "total_tasks": len(tasks),
            "completed_tasks": sum(
                1 for t in tasks if t["task_status"] == "Completed"
            )
        }

        return render_template(
            "onboarding.html",
            onboardings=onboardings,
            tasks_by_onboarding=tasks_by_onboarding,
            available_employees=available_employees,
            summary=summary
        )

    except Exception as error:
        print("Onboarding loading error:", error)
        flash("Unable to load onboarding records.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/hr/onboarding/create", methods=["POST"])
@login_required
@role_required("HR Admin", "Manager")
def create_onboarding():

    from datetime import datetime

    conn = None
    cursor = None

    try:
        employee_id = int(request.form.get("employee_id", ""))
        start_date = datetime.strptime(
            request.form.get("start_date", ""), "%Y-%m-%d"
        ).date()
        notes = request.form.get("notes", "").strip() or None

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT employee_id
            FROM employees
            WHERE employee_id = %s
              AND employment_status = 'Active'
        """, (employee_id,))

        if not cursor.fetchone():
            raise ValueError("Select a valid active employee.")

        cursor.execute("""
            INSERT INTO onboarding_records (
                employee_id,
                onboarding_status,
                start_date,
                assigned_hr,
                notes
            )
            VALUES (%s, 'Not Started', %s, %s, %s)
        """, (
            employee_id,
            start_date,
            session["user_id"],
            notes
        ))

        conn.commit()
        flash("Employee onboarding record created successfully.", "success")

    except ValueError as error:
        if conn:
            conn.rollback()
        flash(str(error) or "Enter valid onboarding details.", "danger")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Onboarding creation error:", error)
        flash(
            "Unable to create onboarding record. The employee may already have an onboarding record.",
            "danger"
        )

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("onboarding_management"))


@app.route(
    "/hr/onboarding/<int:onboarding_id>/tasks/create",
    methods=["POST"]
)
@login_required
@role_required("HR Admin", "Manager")
def create_onboarding_task(onboarding_id):

    from datetime import datetime

    conn = None
    cursor = None

    allowed_categories = (
        "Documentation",
        "IT Setup",
        "HR Orientation",
        "Training",
        "Administration"
    )

    try:
        task_title = request.form.get("task_title", "").strip()
        task_category = request.form.get(
            "task_category", "Administration"
        )
        due_value = request.form.get("due_date", "").strip()
        task_notes = request.form.get("task_notes", "").strip() or None

        if not task_title:
            raise ValueError("Task title is required.")

        if task_category not in allowed_categories:
            raise ValueError("Invalid task category.")

        due_date = (
            datetime.strptime(due_value, "%Y-%m-%d").date()
            if due_value else None
        )

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT onboarding_status
            FROM onboarding_records
            WHERE onboarding_id = %s
        """, (onboarding_id,))

        onboarding = cursor.fetchone()

        if not onboarding:
            raise ValueError("Onboarding record not found.")

        if onboarding[0] == "Completed":
            raise ValueError("Cannot add tasks to completed onboarding.")

        cursor.execute("""
            INSERT INTO onboarding_tasks (
                onboarding_id,
                task_title,
                task_category,
                due_date,
                assigned_to,
                task_notes
            )
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            onboarding_id,
            task_title,
            task_category,
            due_date,
            session["user_id"],
            task_notes
        ))

        conn.commit()
        flash("Onboarding task added successfully.", "success")

    except ValueError as error:
        if conn:
            conn.rollback()
        flash(str(error) or "Enter valid task details.", "danger")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Onboarding task creation error:", error)
        flash("Unable to add onboarding task.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("onboarding_management"))


@app.route(
    "/hr/onboarding/tasks/<int:task_id>/status",
    methods=["POST"]
)
@login_required
@role_required("HR Admin", "Manager")
def update_onboarding_task(task_id):

    allowed_statuses = ("Pending", "In Progress", "Completed")

    new_status = request.form.get("task_status")

    if new_status not in allowed_statuses:
        flash("Invalid task status.", "danger")
        return redirect(url_for("onboarding_management"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT onboarding_id
            FROM onboarding_tasks
            WHERE task_id = %s
        """, (task_id,))

        task = cursor.fetchone()

        if not task:
            flash("Onboarding task not found.", "warning")
            return redirect(url_for("onboarding_management"))

        onboarding_id = task["onboarding_id"]

        cursor.execute("""
            UPDATE onboarding_tasks
            SET task_status = %s,
                completed_at = CASE
                    WHEN %s = 'Completed' THEN NOW()
                    ELSE NULL
                END
            WHERE task_id = %s
        """, (new_status, new_status, task_id))

        cursor.execute("""
            SELECT
                COUNT(*) AS total_tasks,
                SUM(task_status = 'Completed') AS completed_tasks,
                SUM(task_status = 'In Progress') AS active_tasks
            FROM onboarding_tasks
            WHERE onboarding_id = %s
        """, (onboarding_id,))

        task_summary = cursor.fetchone()

        total = task_summary["total_tasks"] or 0
        completed = task_summary["completed_tasks"] or 0
        active = task_summary["active_tasks"] or 0

        if total > 0 and completed == total:
            onboarding_status = "Completed"
        elif completed > 0 or active > 0:
            onboarding_status = "In Progress"
        else:
            onboarding_status = "Not Started"

        cursor.execute("""
            UPDATE onboarding_records
            SET onboarding_status = %s,
                completion_date = CASE
                    WHEN %s = 'Completed' THEN CURDATE()
                    ELSE NULL
                END
            WHERE onboarding_id = %s
        """, (
            onboarding_status,
            onboarding_status,
            onboarding_id
        ))

        conn.commit()
        flash("Task and onboarding progress updated successfully.", "success")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Onboarding task update error:", error)
        flash("Unable to update onboarding task.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("onboarding_management"))

@app.route("/hr/access")
@login_required
@role_required("HR Admin")
def access_management():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, username, role, employee_id
            FROM users
            ORDER BY username
        """)
        user_accounts = cursor.fetchall()

        cursor.execute("""
            SELECT e.employee_id, e.employee_code,
                   e.first_name, e.last_name
            FROM employees e
            LEFT JOIN users u ON e.employee_id = u.employee_id
            WHERE u.user_id IS NULL
            ORDER BY e.first_name, e.last_name
        """)
        available_employees = cursor.fetchall()

        role_summary = {
            "HR Admin": 0,
            "Manager": 0,
            "Employee": 0
        }

        for user in user_accounts:
            if user["role"] in role_summary:
                role_summary[user["role"]] += 1

        return render_template(
            "access.html",
            user_accounts=user_accounts,
            role_summary=role_summary,
            available_employees=available_employees
        )

    except Exception:
        app.logger.exception("Access management error")
        flash("Unable to load user accounts.", "danger")
        return redirect(url_for("dashboard"))

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
@app.route("/hr/access/users/create", methods=["POST"])
@login_required
@role_required("HR Admin")
def create_user_account():

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "")
    employee_id = request.form.get("employee_id", "").strip()

    allowed_roles = ("HR Admin", "Manager", "Employee")

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        flash("Invalid username format.", "danger")
        return redirect(url_for("access_management"))

    if len(password) < 8:
        flash("Password must contain at least 8 characters.", "danger")
        return redirect(url_for("access_management"))

    if role not in allowed_roles:
        flash("Select a valid role.", "danger")
        return redirect(url_for("access_management"))

    if role == "Employee" and not employee_id:
        flash("Select an employee record for this account.", "danger")
        return redirect(url_for("access_management"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT user_id FROM users WHERE username = %s",
            (username,)
        )

        if cursor.fetchone():
            flash("Username already exists.", "warning")
            return redirect(url_for("access_management"))

        linked_employee_id = None

        if role == "Employee":
            try:
                linked_employee_id = int(employee_id)
            except ValueError:
                flash("Invalid employee selection.", "danger")
                return redirect(url_for("access_management"))

            cursor.execute("""
                SELECT employee_id
                FROM employees
                WHERE employee_id = %s
                  AND employment_status = 'Active'
            """, (linked_employee_id,))

            if not cursor.fetchone():
                flash("Select a valid active employee.", "danger")
                return redirect(url_for("access_management"))

            cursor.execute("""
                SELECT user_id
                FROM users
                WHERE employee_id = %s
            """, (linked_employee_id,))

            if cursor.fetchone():
                flash("This employee already has a login account.", "warning")
                return redirect(url_for("access_management"))

        password_hash = generate_password_hash(password)

        cursor.execute("""
            INSERT INTO users (
                username,
                password_hash,
                role,
                employee_id
            )
            VALUES (%s, %s, %s, %s)
        """, (
            username,
            password_hash,
            role,
            linked_employee_id
        ))

        conn.commit()
        flash("User account created successfully.", "success")

    except Exception:
        if conn:
            conn.rollback()

        app.logger.exception("User creation error")
        flash("Unable to create user account.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("access_management"))


@app.route(
    "/hr/access/users/<int:user_id>/role",
    methods=["POST"]
)
@login_required
@role_required("HR Admin")
def update_user_role(user_id):

    new_role = request.form.get("role")

    allowed_roles = ("HR Admin", "Manager", "Employee")

    if new_role not in allowed_roles:
        flash("Invalid user role.", "danger")
        return redirect(url_for("access_management"))

    if user_id == session["user_id"]:
        flash(
            "You cannot change your own role from this screen.",
            "warning"
        )
        return redirect(url_for("access_management"))

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT user_id, username, role
            FROM users
            WHERE user_id = %s
        """, (user_id,))

        target_user = cursor.fetchone()

        if not target_user:
            flash("User account not found.", "warning")
            return redirect(url_for("access_management"))

        # Protect the final HR Admin account.
        if target_user["role"] == "HR Admin" and new_role != "HR Admin":

            cursor.execute("""
                SELECT COUNT(*) AS admin_count
                FROM users
                WHERE role = 'HR Admin'
            """)

            admin_count = cursor.fetchone()["admin_count"]

            if admin_count <= 1:
                flash(
                    "The last HR Admin account cannot be demoted.",
                    "danger"
                )
                return redirect(url_for("access_management"))

        cursor.execute("""
            UPDATE users
            SET role = %s
            WHERE user_id = %s
        """, (new_role, user_id))

        conn.commit()
        flash(
            f"Role updated for {target_user['username']}.",
            "success"
        )

    except Exception as error:
        if conn:
            conn.rollback()

        print("Role update error:", error)
        flash("Unable to update user role.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("access_management"))


@app.route("/employee/portal")
@login_required
@role_required("Employee")
def employee_portal():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                e.employee_id,
                e.employee_code,
                e.first_name,
                e.last_name,
                e.email,
                e.designation,
                e.joining_date,
                e.employment_status,
                d.department_name
            FROM users u
            JOIN employees e
                ON u.employee_id = e.employee_id
            LEFT JOIN departments d
                ON e.department_id = d.department_id
            WHERE u.user_id = %s
              AND u.role = 'Employee'
        """, (session["user_id"],))

        employee = cursor.fetchone()

        if not employee:
            return (
                "Employee account is not linked to a valid employee record. "
                "Please contact HR.",
                403
            )

        # The current employee registration route does not store phone values.
        # Supply a safe empty value without requiring an employees.phone column.
        employee["phone"] = None

        cursor.execute("""
            SELECT
                leave_id, leave_type, start_date, end_date,
                reason, status, applied_at
            FROM leave_requests
            WHERE employee_id = %s
            ORDER BY applied_at DESC
        """, (employee["employee_id"],))
        leave_requests = cursor.fetchall()

        cursor.execute("""
            SELECT
                attendance_date, check_in, check_out,
                attendance_status, remarks
            FROM attendance_records
            WHERE employee_id = %s
            ORDER BY attendance_date DESC
            LIMIT 10
        """, (employee["employee_id"],))
        attendance_records = cursor.fetchall()

        leave_summary = {
            "total": len(leave_requests),
            "pending": sum(1 for leave in leave_requests if leave["status"] == "Pending"),
            "approved": sum(1 for leave in leave_requests if leave["status"] == "Approved")
        }

        return render_template(
            "employee_portal.html",
            employee=employee,
            leave_requests=leave_requests,
            attendance_records=attendance_records,
            leave_summary=leave_summary
        )

    except Exception as error:
        app.logger.exception("Employee portal error")
        # Do not redirect to dashboard here: the Employee dashboard redirects
        # back to this route and would create ERR_TOO_MANY_REDIRECTS.
        return "Employee Portal could not load. Check the Flask terminal for details.", 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# --------------------------------------------------
# EMPLOYEE LEAVE APPLICATION
@login_required
@role_required("Employee")
def employee_apply_leave():

    from datetime import datetime

    conn = None
    cursor = None

    allowed_types = (
        "Casual Leave",
        "Sick Leave",
        "Annual Leave",
        "Unpaid Leave",
        "Maternity Leave",
        "Paternity Leave"
    )

    try:
        leave_type = request.form.get("leave_type")
        start_date = datetime.strptime(
            request.form.get("start_date", ""), "%Y-%m-%d"
        ).date()
        end_date = datetime.strptime(
            request.form.get("end_date", ""), "%Y-%m-%d"
        ).date()
        reason = request.form.get("reason", "").strip() or None

        if leave_type not in allowed_types:
            raise ValueError("Select a valid leave type.")

        if end_date < start_date:
            raise ValueError("End date cannot be before start date.")

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT employee_id
            FROM users
            WHERE user_id = %s
              AND role = 'Employee'
              AND employee_id IS NOT NULL
        """, (session["user_id"],))

        linked_employee = cursor.fetchone()

        if not linked_employee:
            raise ValueError("Your login is not linked to an employee record.")

        employee_id = linked_employee[0]

        cursor.execute("""
            SELECT leave_id
            FROM leave_requests
            WHERE employee_id = %s
              AND status IN ('Pending', 'Approved')
              AND start_date <= %s
              AND end_date >= %s
            LIMIT 1
        """, (employee_id, end_date, start_date))

        if cursor.fetchone():
            raise ValueError(
                "You already have a pending or approved request "
                "overlapping these dates."
            )

        cursor.execute("""
            INSERT INTO leave_requests (
                employee_id,
                leave_type,
                start_date,
                end_date,
                reason
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            employee_id,
            leave_type,
            start_date,
            end_date,
            reason
        ))

        conn.commit()
        flash("Your leave request has been submitted to HR.", "success")

    except ValueError as error:
        if conn:
            conn.rollback()
        flash(str(error), "danger")

    except Exception as error:
        if conn:
            conn.rollback()
        print("Employee leave submission error:", error)
        flash("Unable to submit your leave request.", "danger")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return redirect(url_for("employee_portal"))



@app.route("/employee/compensation")
@login_required
@role_required("Employee")
def employee_compensation():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT employee_id
            FROM users
            WHERE user_id = %s
              AND role = 'Employee'
              AND employee_id IS NOT NULL
        """, (session["user_id"],))

        employee = cursor.fetchone()

        if not employee:
            return "Employee account is not linked to an employee record. Contact HR.", 403

        cursor.execute("""
            SELECT
                pay_period, basic_salary, allowances, deductions,
                net_salary, payment_status, payment_date
            FROM payroll_records
            WHERE employee_id = %s
            ORDER BY pay_period DESC
            LIMIT 12
        """, (employee["employee_id"],))
        payroll_records = cursor.fetchall()

        return render_template(
            "employee_compensation.html",
            payroll_records=payroll_records
        )

    except Exception:
        app.logger.exception("Employee compensation error")
        return "Employee compensation page could not load. Check the Flask terminal for details.", 500

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route("/employee/leave/apply", methods=["POST"])
@login_required
@role_required("Employee")
def employee_apply_leave():

    conn = None
    cursor = None

    allowed_types = (
        "Casual Leave",
        "Sick Leave",
        "Annual Leave",
        "Unpaid Leave",
        "Maternity Leave",
        "Paternity Leave"
    )

    try:
        leave_type = request.form.get("leave_type")

        start_date = datetime.strptime(
            request.form.get("start_date", ""),
            "%Y-%m-%d"
        ).date()

        end_date = datetime.strptime(
            request.form.get("end_date", ""),
            "%Y-%m-%d"
        ).date()

        reason = request.form.get("reason", "").strip() or None

        if leave_type not in allowed_types:
            raise ValueError("Select a valid leave type.")

        if end_date < start_date:
            raise ValueError("End date cannot be before start date.")

        conn = get_db_connection()
        cursor = conn.cursor()

        # Get employee ID from the authenticated account.
        cursor.execute("""
            SELECT employee_id
            FROM users
            WHERE user_id = %s
              AND role = 'Employee'
              AND employee_id IS NOT NULL
        """, (session["user_id"],))

        linked_employee = cursor.fetchone()

        if not linked_employee:
            raise ValueError("Your account is not linked to an employee.")

        employee_id = linked_employee[0]

        # Prevent overlapping pending or approved leave requests.
        cursor.execute("""
            SELECT leave_id
            FROM leave_requests
            WHERE employee_id = %s
              AND status IN ('Pending', 'Approved')
              AND start_date <= %s
              AND end_date >= %s
            LIMIT 1
        """, (employee_id, end_date, start_date))

        if cursor.fetchone():
            raise ValueError(
                "You already have a pending or approved leave "
                "request for overlapping dates."
            )

        # Save the employee's leave request.
        cursor.execute("""
            INSERT INTO leave_requests (
                employee_id,
                leave_type,
                start_date,
                end_date,
                reason
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            employee_id,
            leave_type,
            start_date,
            end_date,
            reason
        ))

        conn.commit()

        flash(
            "Your leave request has been submitted to HR.",
            "success"
        )

    except ValueError as error:
        if conn:
            conn.rollback()

        flash(str(error), "danger")

    except Exception as error:
        if conn:
            conn.rollback()

        app.logger.exception("Employee leave submission error")
        flash("Unable to submit your leave request.", "danger")

    finally:
        if cursor:
            cursor.close()

        if conn:
            conn.close()

    return redirect(url_for("employee_portal"))

# --------------------------------------------------
# RUN APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
