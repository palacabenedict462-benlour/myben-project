"""
Sitio Clearance Ticket Service — Web API
Flask layer that wraps the original clreance.py backend (DatabaseManager,
PDFGenerator, and the SITIO_LIST / PURPOSE_OPTIONS constants) so the HTML
UI can talk to it over HTTP instead of the console. This file imports and
reuses clreance.py's classes directly; the only change made to clreance.py
itself was upgrading password hashing to a salted algorithm (see its
hash_password/authenticate_user methods).
"""

import io
import logging
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, session, send_file

import clreance as core  # the original program — DatabaseManager, PDFGenerator, constants

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="static", static_url_path="")
app.config["JSON_SORT_KEYS"] = False

_env_secret = os.environ.get("SECRET_KEY")
if _env_secret:
    app.config["SECRET_KEY"] = _env_secret
else:
    # No SECRET_KEY was set, so generate a random one for this run. That
    # means sessions won't survive a server restart (everyone gets logged
    # out), which is fine for local/demo use. For a real deployment, set
    # the SECRET_KEY environment variable to a fixed, secret value instead.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    logging.warning(
        "No SECRET_KEY environment variable set — generated a temporary one "
        "for this run. Sessions will reset on every restart. Set SECRET_KEY "
        "yourself before deploying this anywhere beyond your own machine."
    )

db = core.DatabaseManager()

ROLE_LABELS = {
    "resident": "Resident",
    "sitio_leader": "Sitio Leader",
    "barangay_officer": "Barangay Officer",
    "admin": "Administrator",
}


def _seed_demo_officer():
    """The console app ships with an admin, 4 sitio leaders, and a resident,
    but no barangay_officer demo account. Add one (once) so the UI can
    demonstrate every role without editing clreance.py's seed data."""
    existing = {u["username"] for u in db.get_all_users()}
    if "officer" not in existing:
        try:
            db.create_user(
                "officer", "officer123", "Barangay Officer Cruz",
                "barangay_officer", "officer@barangay.gov", "09123456790", "Barangay Hall"
            )
        except sqlite3.IntegrityError:
            pass


_seed_demo_officer()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_user(u):
    if not u:
        return None
    return {
        "id": u["id"], "username": u["username"], "full_name": u["full_name"],
        "role": u["role"], "role_label": ROLE_LABELS.get(u["role"], u["role"]),
        "email": u.get("email"), "phone": u.get("phone"), "sitio": u.get("sitio"),
    }


def get_current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Please sign in to continue."}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if request.current_user["role"] not in roles:
                return jsonify({"error": "You don't have permission to do that."}), 403
            return f(*args, **kwargs)
        return wrapper
    return deco


def find_clearance(clearance_id):
    conn = db.get_connection()
    row = conn.execute(
        """SELECT c.*, u.full_name as approved_by_name
           FROM clearances c LEFT JOIN users u ON c.approved_by = u.id
           WHERE c.id = ?""",
        (clearance_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def can_view_clearance(user, clearance):
    if user["role"] in ("barangay_officer", "admin"):
        return True
    if user["role"] == "sitio_leader":
        return clearance["sitio"] == user["sitio"]
    return clearance["user_id"] == user["id"]


def scoped_clearances(user, status=None, search=None):
    if user["role"] == "resident":
        rows = db.get_user_clearances(user["id"])
        if status:
            rows = [r for r in rows if r["status"] == status]
    else:
        filters = {}
        if status:
            filters["status"] = status
        if user["role"] == "sitio_leader":
            filters["sitio"] = user["sitio"]
        rows = db.get_all_clearances(filters)
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["name"].lower() or s in r["clearance_number"].lower()]
    return rows


def notify_admins_and_leaders(sitio, name, clearance_number):
    conn = db.get_connection()
    admins = conn.execute("SELECT id FROM users WHERE role = 'admin'").fetchall()
    leaders = conn.execute(
        "SELECT id FROM users WHERE role = 'sitio_leader' AND sitio = ?", (sitio,)
    ).fetchall()
    conn.close()
    for row in list(admins) + list(leaders):
        db.add_notification(
            row["id"], f"New request in {sitio}",
            f"New clearance request from {name} (Sitio: {sitio})."
        )


def gen_clearance_number():
    import random, string
    return f"CL-{datetime.now().strftime('%Y%m')}-{''.join(random.choices(string.digits, k=4))}"


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"error": "Please enter both username and password."}), 400

    user = db.authenticate_user(username, password)
    if not user:
        return jsonify({"error": "Incorrect username or password."}), 401

    session["user_id"] = user["id"]
    db.log_activity(user["id"], "Login", f"User {user['full_name']} logged in")
    return jsonify(safe_user(user))


@app.route("/api/logout", methods=["POST"])
def logout():
    user = get_current_user()
    if user:
        db.log_activity(user["id"], "Logout", f"User {user['full_name']} logged out")
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Not signed in."}), 401
    return jsonify(safe_user(user))


@app.route("/api/meta")
def meta():
    return jsonify({
        "sitios": core.SITIO_LIST,
        "purposes": core.PURPOSE_OPTIONS,
        "role_labels": ROLE_LABELS,
        "pdf_available": core.PDFGenerator.check_reportlab(),
    })


# ---------------------------------------------------------------------------
# Clearances
# ---------------------------------------------------------------------------
@app.route("/api/clearances")
@login_required
def list_clearances():
    user = request.current_user
    scope = request.args.get("scope", "auto")
    status = request.args.get("status") or None
    search = request.args.get("search") or None

    if scope == "mine":
        rows = db.get_user_clearances(user["id"])
        if status:
            rows = [r for r in rows if r["status"] == status]
        if search:
            s = search.lower()
            rows = [r for r in rows if s in r["name"].lower() or s in r["clearance_number"].lower()]
    else:
        rows = scoped_clearances(user, status=status, search=search)

    rows = sorted(rows, key=lambda r: r["date_requested"] or "", reverse=True)
    return jsonify(rows)


@app.route("/api/clearances", methods=["POST"])
@login_required
def create_clearance():
    user = request.current_user
    data = request.get_json(silent=True) or {}

    required = ["name", "address", "contact", "sitio", "purpose", "date_needed", "id_number"]
    missing = [f for f in required if not str(data.get(f, "")).strip()]
    if missing:
        return jsonify({"error": f"Missing required field(s): {', '.join(missing)}"}), 400

    if data["sitio"] not in core.SITIO_LIST:
        return jsonify({"error": "Invalid sitio selected."}), 400
    if data["purpose"] not in core.PURPOSE_OPTIONS:
        return jsonify({"error": "Invalid purpose selected."}), 400
    try:
        datetime.strptime(data["date_needed"], "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Date needed must be in YYYY-MM-DD format."}), 400

    clearance_number = gen_clearance_number()
    payload = {
        "clearance_number": clearance_number,
        "user_id": user["id"],
        "name": data["name"].strip(),
        "address": data["address"].strip(),
        "contact": data["contact"].strip(),
        "sitio": data["sitio"],
        "purpose": data["purpose"],
        "date_needed": data["date_needed"],
        "id_number": data["id_number"].strip(),
        "remarks": (data.get("remarks") or "").strip(),
        "status": "Pending",
    }
    clearance_id = db.save_clearance(payload)
    db.log_activity(user["id"], "Submit Clearance",
                     f"Submitted clearance {clearance_number} for Sitio {data['sitio']}")
    notify_admins_and_leaders(data["sitio"], payload["name"], clearance_number)

    return jsonify(find_clearance(clearance_id)), 201


@app.route("/api/clearances/<int:clearance_id>")
@login_required
def get_clearance(clearance_id):
    clearance = find_clearance(clearance_id)
    if not clearance:
        return jsonify({"error": "Clearance not found."}), 404
    if not can_view_clearance(request.current_user, clearance):
        return jsonify({"error": "You don't have permission to view this clearance."}), 403
    return jsonify(clearance)


def _transition(clearance_id, allowed_from, new_status, extra=None):
    user = request.current_user
    clearance = find_clearance(clearance_id)
    if not clearance:
        return None, (jsonify({"error": "Clearance not found."}), 404)
    if not can_view_clearance(user, clearance):
        return None, (jsonify({"error": "You don't have permission to act on this clearance."}), 403)
    if clearance["status"] not in allowed_from:
        return None, (jsonify({"error": f"Clearance must be {', '.join(allowed_from)} for this action."}), 409)
    kwargs = dict(extra or {})
    db.update_clearance_status(clearance_id, new_status, **kwargs)
    return clearance, None


@app.route("/api/clearances/<int:clearance_id>/review", methods=["POST"])
@login_required
@roles_required("sitio_leader", "barangay_officer", "admin")
def review_clearance(clearance_id):
    clearance, err = _transition(clearance_id, ["Pending"], "Under Review")
    if err:
        return err
    db.log_activity(request.current_user["id"], "Review Clearance", clearance["clearance_number"])
    db.add_notification(clearance["user_id"], "Clearance under review",
                         f"Your clearance {clearance['clearance_number']} is now under review.")
    return jsonify(find_clearance(clearance_id))


@app.route("/api/clearances/<int:clearance_id>/approve", methods=["POST"])
@login_required
@roles_required("sitio_leader", "barangay_officer", "admin")
def approve_clearance(clearance_id):
    clearance, err = _transition(
        clearance_id, ["Pending", "Under Review"], "Approved",
        extra={"approved_by": request.current_user["id"]}
    )
    if err:
        return err
    db.log_activity(request.current_user["id"], "Approve Clearance", clearance["clearance_number"])
    db.add_notification(clearance["user_id"], "Clearance Approved",
                         f"Your clearance {clearance['clearance_number']} has been approved.")
    return jsonify(find_clearance(clearance_id))


@app.route("/api/clearances/<int:clearance_id>/reject", methods=["POST"])
@login_required
@roles_required("sitio_leader", "barangay_officer", "admin")
def reject_clearance(clearance_id):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip() or "No reason provided."
    clearance, err = _transition(
        clearance_id, ["Pending", "Under Review"], "Rejected",
        extra={"approved_by": request.current_user["id"], "rejection_reason": reason}
    )
    if err:
        return err
    db.log_activity(request.current_user["id"], "Reject Clearance", clearance["clearance_number"])
    db.add_notification(clearance["user_id"], "Clearance Rejected",
                         f"Your clearance {clearance['clearance_number']} was rejected: {reason}")
    return jsonify(find_clearance(clearance_id))


@app.route("/api/clearances/<int:clearance_id>/release", methods=["POST"])
@login_required
@roles_required("barangay_officer", "admin")
def release_clearance(clearance_id):
    clearance, err = _transition(clearance_id, ["Approved"], "Released")
    if err:
        return err
    db.log_activity(request.current_user["id"], "Release Clearance", clearance["clearance_number"])
    db.add_notification(clearance["user_id"], "Certificate released",
                         f"Your clearance {clearance['clearance_number']} has been released. "
                         f"Visit the barangay hall to claim it.")
    return jsonify(find_clearance(clearance_id))


@app.route("/api/clearances/<int:clearance_id>/certificate")
@login_required
def clearance_certificate(clearance_id):
    clearance = find_clearance(clearance_id)
    if not clearance:
        return jsonify({"error": "Clearance not found."}), 404
    if not can_view_clearance(request.current_user, clearance):
        return jsonify({"error": "You don't have permission to view this certificate."}), 403
    if clearance["status"] not in ("Approved", "Released"):
        return jsonify({"error": "Certificate is only available once a clearance is approved."}), 409
    if not core.PDFGenerator.check_reportlab():
        return jsonify({"error": "PDF generation isn't available on the server (reportlab not installed)."}), 501

    # Use the OS's real temp directory instead of a hardcoded "/tmp" — on
    # Windows "/tmp" isn't a valid writable path, which is what silently
    # broke certificate generation there.
    out_path = os.path.join(tempfile.gettempdir(), f"clearance_{clearance['clearance_number']}.pdf")
    try:
        result = core.PDFGenerator.generate_clearance_certificate(clearance, output_path=out_path)
    except Exception as e:
        return jsonify({"error": f"Failed to generate certificate: {e}"}), 500
    if not result:
        return jsonify({"error": "Failed to generate certificate. Check clearance_system.log for details."}), 500
    return send_file(result, as_attachment=True,
                      download_name=f"{clearance['clearance_number']}.pdf",
                      mimetype="application/pdf")


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
@app.route("/api/stats")
@login_required
def stats():
    user = request.current_user
    if user["role"] == "resident":
        rows = db.get_user_clearances(user["id"])
        data = {"total": len(rows), "pending": 0, "under_review": 0, "approved": 0, "released": 0, "rejected": 0}
        for r in rows:
            key = r["status"].lower().replace(" ", "_")
            if key in data:
                data[key] += 1
        return jsonify(data)
    if user["role"] == "sitio_leader":
        return jsonify(db.get_statistics(sitio_filter=user["sitio"]))
    return jsonify(db.get_statistics())


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@app.route("/api/notifications")
@login_required
def list_notifications():
    return jsonify(db.get_notifications(request.current_user["id"]))


@app.route("/api/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def read_notification(notif_id):
    db.mark_notification_read(notif_id)
    return jsonify({"ok": True})


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def read_all_notifications():
    for n in db.get_notifications(request.current_user["id"], unread_only=True):
        db.mark_notification_read(n["id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@app.route("/api/reports/export")
@login_required
@roles_required("sitio_leader", "barangay_officer", "admin")
def export_report():
    scope = request.args.get("scope", "all")
    status = "Pending" if scope == "pending" else None
    rows = scoped_clearances(request.current_user, status=status)

    import json
    buf = io.BytesIO(json.dumps(rows, indent=2, default=str).encode("utf-8"))
    buf.seek(0)
    filename = f"{'pending_requests' if scope == 'pending' else 'clearances_export'}_{datetime.now().strftime('%Y%m%d')}.json"
    return send_file(buf, as_attachment=True, download_name=filename, mimetype="application/json")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@app.route("/api/settings")
@login_required
@roles_required("barangay_officer", "admin")
def settings():
    return jsonify({
        "system_name": "Sitio Clearance Ticket Service",
        "version": "2.0",
        "database": "SQLite",
        "pdf_available": core.PDFGenerator.check_reportlab(),
        "sitios": core.SITIO_LIST,
    })


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------
@app.route("/api/users")
@login_required
@roles_required("admin")
def list_users():
    return jsonify(db.get_all_users())


@app.route("/api/users", methods=["POST"])
@login_required
@roles_required("admin")
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    full_name = (data.get("full_name") or "").strip()
    role = data.get("role")
    sitio = data.get("sitio")

    if not username or not password or not full_name:
        return jsonify({"error": "Username, password, and full name are required."}), 400
    if role not in ROLE_LABELS:
        return jsonify({"error": "Invalid role."}), 400
    if role != "admin" and sitio not in core.SITIO_LIST:
        return jsonify({"error": "Invalid sitio."}), 400

    try:
        user_id = db.create_user(username, password, full_name, role,
                                  data.get("email"), data.get("phone"),
                                  None if role == "admin" else sitio)
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists."}), 409

    db.log_activity(request.current_user["id"], "Create User", f"Created user {username} ({role})")
    conn = db.get_connection()
    row = conn.execute("SELECT id, username, full_name, role, email, phone, sitio, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return jsonify(dict(row)), 201


if __name__ == "__main__":
    # host="0.0.0.0" makes the server reachable from other devices on the
    # same network (like your phone), not just this computer. It's still
    # only visible on your local WiFi/LAN — not the public internet.
    #
    # debug=True enables Flask's interactive debugger, which lets anyone
    # who can reach this server run arbitrary Python code if they trigger
    # an error page. That's an acceptable trade-off on your own trusted
    # home/office WiFi for local testing, but should be OFF for anything
    # more exposed. Override with:  FLASK_DEBUG=0 python3 app.py
    debug_mode = os.environ.get("FLASK_DEBUG", "1") != "0"
    if debug_mode:
        logging.warning(
            "Running with debug=True. Do not expose this server beyond a "
            "network you fully trust — the debugger allows code execution. "
            "Run with FLASK_DEBUG=0 to disable it."
        )
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)
