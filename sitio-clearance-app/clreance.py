"""
Sitio Clearance Ticket Service - Barangay Lapasan
Console-based version with all functionality
"""

import json
import os
import random
import string
import sqlite3
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from enum import Enum

try:
    from werkzeug.security import generate_password_hash, check_password_hash
    _HAS_WERKZEUG = True
except ImportError:
    # Console-only environments without Flask/Werkzeug installed fall back
    # to the old unsalted SHA-256 scheme so the program still runs.
    _HAS_WERKZEUG = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('clearance_system.log'),
        logging.StreamHandler()
    ]
)

# Constants
SITIO_LIST = [
    "SAN LAZARO",
    "LAPAZ UNO",
    "STO NIÑO",
    "KOLAMBOG"
]

PURPOSE_OPTIONS = [
    "Barangay Clearance",
    "Certificate of Residency",
    "Certificate of Indigency",
    "Barangay Business Permit",
    "Certificate of Good Moral Character",
    "Other"
]

class UserRole(Enum):
    RESIDENT = "resident"
    SITIO_LEADER = "sitio_leader"
    BARANGAY_OFFICER = "barangay_officer"
    ADMIN = "admin"

class ClearanceStatus(Enum):
    PENDING = "Pending"
    UNDER_REVIEW = "Under Review"
    APPROVED = "Approved"
    RELEASED = "Released"
    REJECTED = "Rejected"


class DatabaseManager:
    def __init__(self, db_path="clearance_system.db"):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                sitio TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Clearance requests table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clearance_number TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                contact TEXT NOT NULL,
                sitio TEXT NOT NULL,
                purpose TEXT NOT NULL,
                date_needed DATE NOT NULL,
                id_number TEXT NOT NULL,
                remarks TEXT,
                status TEXT DEFAULT 'Pending',
                date_requested TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                date_approved TIMESTAMP,
                date_released TIMESTAMP,
                approved_by INTEGER,
                rejection_reason TEXT,
                qr_code_path TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (approved_by) REFERENCES users (id)
            )
        ''')
        
        # Activity logs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Notifications table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Create admin user if not exists
        admin_password = self.hash_password("admin123")
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password_hash, full_name, role, email, phone, sitio)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("admin", admin_password, "System Administrator", "admin", "admin@barangay.gov", "09123456789", "Barangay Hall"))
        
        # Create default sitio leaders for each sitio
        sitio_leader_password = self.hash_password("leader123")
        sitio_leaders = [
            ("sanlazaro", "San Lazaro Leader", "sanlazaro@barangay.gov", "09123456780", "SAN LAZARO"),
            ("lapazuno", "Lapaz Uno Leader", "lapazuno@barangay.gov", "09123456781", "LAPAZ UNO"),
            ("stonino", "Sto Niño Leader", "stonino@barangay.gov", "09123456782", "STO NIÑO"),
            ("kolambog", "Kolambog Leader", "kolambog@barangay.gov", "09123456783", "KOLAMBOG")
        ]
        
        for username, full_name, email, phone, sitio in sitio_leaders:
            cursor.execute('''
                INSERT OR IGNORE INTO users (username, password_hash, full_name, role, email, phone, sitio)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (username, sitio_leader_password, full_name, "sitio_leader", email, phone, sitio))
        
        # Create a default resident
        resident_password = self.hash_password("resident123")
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password_hash, full_name, role, email, phone, sitio)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("resident", resident_password, "John Doe", "resident", "resident@email.com", "09123456784", "SAN LAZARO"))
        
        # Create sample clearances if none exist
        cursor.execute("SELECT COUNT(*) FROM clearances")
        count = cursor.fetchone()[0]
        
        if count == 0:
            sample_clearances = [
                ("CL-202601-0001", 4, "Maria Santos", "123 Main St", "09123456785", "SAN LAZARO", "Barangay Clearance", "2026-02-15", "ID-001", "", "Pending"),
                ("CL-202601-0002", 4, "Juan Dela Cruz", "456 Rizal Ave", "09123456786", "LAPAZ UNO", "Certificate of Residency", "2026-02-20", "ID-002", "", "Pending"),
                ("CL-202601-0003", 4, "Ana Reyes", "789 Mabini St", "09123456787", "STO NIÑO", "Certificate of Indigency", "2026-02-25", "ID-003", "", "Approved"),
                ("CL-202601-0004", 4, "Pedro Gomez", "321 Luna St", "09123456788", "KOLAMBOG", "Barangay Business Permit", "2026-03-01", "ID-004", "", "Pending"),
                ("CL-202601-0005", 4, "Rosa Santos", "555 Quezon Blvd", "09123456789", "SAN LAZARO", "Certificate of Good Moral Character", "2026-03-05", "ID-005", "", "Approved"),
            ]
            
            for clearance in sample_clearances:
                cursor.execute('''
                    INSERT INTO clearances (
                        clearance_number, user_id, name, address, contact, sitio,
                        purpose, date_needed, id_number, remarks, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', clearance)
        
        conn.commit()
        conn.close()
        logging.info("Database initialized successfully with default accounts and sample data")
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password with a salted algorithm (PBKDF2-SHA256 via
        Werkzeug) when available, falling back to unsalted SHA-256 only if
        Werkzeug isn't installed (e.g. running clreance.py with no
        dependencies installed). The fallback is marked so authenticate_user
        can tell the two formats apart."""
        if _HAS_WERKZEUG:
            return generate_password_hash(password)
        return "sha256$" + hashlib.sha256(password.encode()).hexdigest()

    def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """Authenticate user and return user data"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()

        if not user:
            return None

        stored = user['password_hash']
        if stored.startswith('sha256$'):
            ok = stored == "sha256$" + hashlib.sha256(password.encode()).hexdigest()
        elif _HAS_WERKZEUG:
            ok = check_password_hash(stored, password)
        else:
            ok = False

        return dict(user) if ok else None
    
    def save_clearance(self, data: Dict) -> int:
        """Save clearance request and return ID"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO clearances (
                clearance_number, user_id, name, address, contact, sitio,
                purpose, date_needed, id_number, remarks, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['clearance_number'],
            data.get('user_id'),
            data['name'],
            data['address'],
            data['contact'],
            data['sitio'],
            data['purpose'],
            data['date_needed'],
            data['id_number'],
            data.get('remarks', ''),
            data.get('status', 'Pending')
        ))
        
        clearance_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return clearance_id
    
    def update_clearance_status(self, clearance_id: int, status: str, 
                                approved_by: int = None, rejection_reason: str = None):
        """Update clearance status"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        updates = []
        params = []
        
        updates.append("status = ?")
        params.append(status)
        
        if status == "Approved":
            updates.append("date_approved = CURRENT_TIMESTAMP")
        elif status == "Rejected":
            updates.append("rejection_reason = ?")
            params.append(rejection_reason)
        elif status == "Released":
            updates.append("date_released = CURRENT_TIMESTAMP")
        
        if approved_by:
            updates.append("approved_by = ?")
            params.append(approved_by)
        
        params.append(clearance_id)
        
        cursor.execute(f'''
            UPDATE clearances 
            SET {', '.join(updates)}
            WHERE id = ?
        ''', params)
        
        conn.commit()
        conn.close()
    
    def get_all_clearances(self, filters: Dict = None) -> List[Dict]:
        """Get all clearances with optional filters"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT c.*, u.full_name as approved_by_name
            FROM clearances c
            LEFT JOIN users u ON c.approved_by = u.id
            WHERE 1=1
        '''
        params = []
        
        if filters:
            if 'status' in filters:
                query += " AND c.status = ?"
                params.append(filters['status'])
            if 'sitio' in filters:
                query += " AND c.sitio = ?"
                params.append(filters['sitio'])
            if 'date_from' in filters:
                query += " AND c.date_requested >= ?"
                params.append(filters['date_from'])
            if 'date_to' in filters:
                query += " AND c.date_requested <= ?"
                params.append(filters['date_to'])
            if 'search' in filters:
                query += " AND (c.name LIKE ? OR c.clearance_number LIKE ?)"
                search = f"%{filters['search']}%"
                params.extend([search, search])
        
        query += " ORDER BY c.date_requested DESC"
        
        cursor.execute(query, params)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return results
    
    def get_user_clearances(self, user_id: int) -> List[Dict]:
        """Get clearances for specific user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM clearances 
            WHERE user_id = ? 
            ORDER BY date_requested DESC
        ''', (user_id,))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return results
    
    def add_notification(self, user_id: int, title: str, message: str):
        """Add notification for user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO notifications (user_id, title, message)
            VALUES (?, ?, ?)
        ''', (user_id, title, message))
        
        conn.commit()
        conn.close()
    
    def get_notifications(self, user_id: int, unread_only: bool = False) -> List[Dict]:
        """Get user notifications"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT * FROM notifications 
            WHERE user_id = ?
        '''
        if unread_only:
            query += " AND is_read = 0"
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query, (user_id,))
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return results
    
    def mark_notification_read(self, notification_id: int):
        """Mark notification as read"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE notifications SET is_read = 1
            WHERE id = ?
        ''', (notification_id,))
        
        conn.commit()
        conn.close()
    
    def log_activity(self, user_id: int, action: str, details: str = None):
        """Log user activity"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO activity_logs (user_id, action, details)
            VALUES (?, ?, ?)
        ''', (user_id, action, details))
        
        conn.commit()
        conn.close()
    
    def get_statistics(self, sitio_filter: str = None) -> Dict:
        """Get system statistics with optional sitio filter"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        stats = {}
        params = []
        
        where_clause = ""
        if sitio_filter:
            where_clause = " WHERE sitio = ?"
            params.append(sitio_filter)
        
        # Total clearances
        cursor.execute(f"SELECT COUNT(*) FROM clearances{where_clause}", params)
        stats['total'] = cursor.fetchone()[0] or 0
        
        # Status counts
        for status in ClearanceStatus:
            if sitio_filter:
                status_params = params + [status.value]
                cursor.execute(f"SELECT COUNT(*) FROM clearances{where_clause} AND status = ?", status_params)
            else:
                cursor.execute("SELECT COUNT(*) FROM clearances WHERE status = ?", (status.value,))
            stats[status.value.lower().replace(' ', '_')] = cursor.fetchone()[0] or 0
        
        # Sitio distribution
        if not sitio_filter:
            cursor.execute('''
                SELECT sitio, COUNT(*) as count 
                FROM clearances 
                GROUP BY sitio 
                ORDER BY count DESC
            ''')
            stats['sitio_distribution'] = [dict(row) for row in cursor.fetchall()]
        else:
            stats['sitio_distribution'] = []
        
        # Monthly trend
        if sitio_filter:
            cursor.execute(f'''
                SELECT strftime('%Y-%m', date_requested) as month, COUNT(*) as count
                FROM clearances{where_clause}
                GROUP BY month
                ORDER BY month DESC
                LIMIT 6
            ''', params)
        else:
            cursor.execute('''
                SELECT strftime('%Y-%m', date_requested) as month, COUNT(*) as count
                FROM clearances
                GROUP BY month
                ORDER BY month DESC
                LIMIT 6
            ''')
        stats['monthly_trend'] = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        return stats
    
    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, username, full_name, role, email, phone, sitio, created_at
            FROM users
            ORDER BY created_at DESC
        ''')
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
    
    def create_user(self, username: str, password: str, full_name: str, 
                   role: str, email: str = None, phone: str = None, sitio: str = None) -> int:
        """Create a new user"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        password_hash = self.hash_password(password)
        cursor.execute('''
            INSERT INTO users (username, password_hash, full_name, role, email, phone, sitio)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (username, password_hash, full_name, role, email, phone, sitio))
        
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return user_id
    
    def get_users_by_sitio(self, sitio: str) -> List[Dict]:
        """Get users by sitio"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, username, full_name, role, email, phone, sitio
            FROM users
            WHERE sitio = ?
            ORDER BY full_name
        ''', (sitio,))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results


class PDFGenerator:
    """Simplified PDF generator using reportlab"""
    
    @staticmethod
    def check_reportlab():
        """Check if reportlab is installed"""
        try:
            import reportlab
            return True
        except ImportError:
            return False
    
    @staticmethod
    def generate_clearance_certificate(clearance_data: Dict, output_path: str = None) -> str:
        """Generate a clearance certificate PDF"""
        try:
            if not PDFGenerator.check_reportlab():
                logging.error("reportlab not installed")
                return None
            
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.lib.enums import TA_CENTER
            
            if output_path is None:
                output_path = f"clearance_{clearance_data['clearance_number']}.pdf"
            
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            story = []
            styles = getSampleStyleSheet()
            
            center_style = ParagraphStyle(
                'CenterStyle',
                parent=styles['Normal'],
                alignment=TA_CENTER,
                fontSize=12,
                spaceAfter=10
            )
            
            story.append(Paragraph("<b>REPUBLIC OF THE PHILIPPINES</b>", center_style))
            story.append(Paragraph("<b>BARANGAY LAPASAN</b>", center_style))
            story.append(Paragraph("<b>SITIO CLEARANCE CERTIFICATE</b>", center_style))
            story.append(Spacer(1, 0.2*inch))
            
            story.append(Paragraph(
                f"<b>Clearance No.:</b> {clearance_data['clearance_number']}",
                styles['Normal']
            ))
            story.append(Paragraph(
                f"<b>Date Issued:</b> {datetime.now().strftime('%B %d, %Y')}",
                styles['Normal']
            ))
            story.append(Spacer(1, 0.3*inch))
            
            story.append(Paragraph(
                "TO WHOM IT MAY CONCERN:",
                styles['Heading2']
            ))
            story.append(Spacer(1, 0.1*inch))
            
            cert_text = f"""
            This is to certify that <b>{clearance_data['name']}</b>, 
            a resident of {clearance_data['address']}, Sitio {clearance_data['sitio']}, 
            Barangay Lapasan, has been cleared and is authorized to 
            <b>{clearance_data['purpose']}</b>.
            """
            story.append(Paragraph(cert_text, styles['Normal']))
            story.append(Spacer(1, 0.2*inch))
            
            story.append(Paragraph(
                f"<b>Contact Number:</b> {clearance_data['contact']}",
                styles['Normal']
            ))
            story.append(Paragraph(
                f"<b>ID Number:</b> {clearance_data['id_number']}",
                styles['Normal']
            ))
            story.append(Paragraph(
                f"<b>Status:</b> {clearance_data['status']}",
                styles['Normal']
            ))
            story.append(Spacer(1, 0.3*inch))
            
            if clearance_data.get('remarks'):
                story.append(Paragraph(
                    f"<b>Remarks:</b> {clearance_data['remarks']}",
                    styles['Normal']
                ))
                story.append(Spacer(1, 0.2*inch))
            
            story.append(Spacer(1, 0.5*inch))
            
            sig_data = [
                ["_______________________", "_______________________"],
                ["SITIO LEADER", "BARANGAY CAPTAIN"],
                ["Date: _____________", "Date: _____________"]
            ]
            
            sig_table = Table(sig_data, colWidths=[2.5*inch, 2.5*inch])
            sig_table.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ]))
            story.append(sig_table)
            
            story.append(Spacer(1, 0.3*inch))
            story.append(Paragraph(
                "<i>This certificate is issued upon request of the above-named person for whatever legal purpose it may serve.</i>",
                center_style
            ))
            
            doc.build(story)
            logging.info(f"PDF generated: {output_path}")
            return output_path
            
        except Exception as e:
            logging.error(f"PDF generation error: {e}")
            return None
    
    @staticmethod
    def generate_clearance_report(clearances: List[Dict], report_type: str = "monthly", 
                                  date_range: str = None, output_path: str = None) -> str:
        """Generate a clearance report PDF"""
        try:
            if not PDFGenerator.check_reportlab():
                logging.error("reportlab not installed")
                return None
            
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.lib.enums import TA_CENTER
            
            if output_path is None:
                output_path = f"clearance_report_{datetime.now().strftime('%Y%m%d')}.pdf"
            
            doc = SimpleDocTemplate(
                output_path,
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            story = []
            styles = getSampleStyleSheet()
            
            title_style = ParagraphStyle(
                'ReportTitle',
                parent=styles['Title'],
                alignment=TA_CENTER,
                fontSize=16,
                spaceAfter=20
            )
            
            story.append(Paragraph(
                f"<b>SITIO CLEARANCE SYSTEM - {report_type.upper()} REPORT</b>",
                title_style
            ))
            
            if date_range:
                story.append(Paragraph(
                    f"<b>Report Period:</b> {date_range}",
                    styles['Normal']
                ))
            else:
                story.append(Paragraph(
                    f"<b>Generated:</b> {datetime.now().strftime('%B %d, %Y %H:%M')}",
                    styles['Normal']
                ))
            
            story.append(Spacer(1, 0.2*inch))
            
            if clearances:
                total = len(clearances)
                approved = len([c for c in clearances if c.get('status') == 'Approved'])
                pending = len([c for c in clearances if c.get('status') == 'Pending'])
                rejected = len([c for c in clearances if c.get('status') == 'Rejected'])
                
                story.append(Paragraph("<b>SUMMARY STATISTICS</b>", styles['Heading2']))
                summary_data = [
                    ["Total Requests", str(total)],
                    ["Approved", str(approved)],
                    ["Pending", str(pending)],
                    ["Rejected", str(rejected)]
                ]
                
                summary_table = Table(summary_data, colWidths=[2*inch, 1*inch])
                summary_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                story.append(summary_table)
                story.append(Spacer(1, 0.3*inch))
                
                story.append(Paragraph("<b>DETAILED DATA</b>", styles['Heading2']))
                
                table_data = [["#", "Name", "Purpose", "Sitio", "Status", "Date"]]
                for i, clearance in enumerate(clearances[:100], 1):
                    table_data.append([
                        str(i),
                        clearance.get('name', '')[:30],
                        clearance.get('purpose', '')[:25],
                        clearance.get('sitio', ''),
                        clearance.get('status', ''),
                        clearance.get('date_requested', '')[:10] if clearance.get('date_requested') else ''
                    ])
                
                detailed_table = Table(table_data, colWidths=[0.5*inch, 1.5*inch, 1.5*inch, 1*inch, 1*inch, 1*inch])
                detailed_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ]))
                story.append(detailed_table)
                
                if len(clearances) > 100:
                    story.append(Paragraph(
                        f"<i>Showing first 100 of {len(clearances)} records.</i>",
                        styles['Normal']
                    ))
            
            story.append(Spacer(1, 0.5*inch))
            story.append(Paragraph(
                f"Generated by Barangay Lapasan Sitio Clearance System v2.0",
                styles['Normal']
            ))
            
            doc.build(story)
            logging.info(f"Report PDF generated: {output_path}")
            return output_path
            
        except Exception as e:
            logging.error(f"Report PDF generation error: {e}")
            return None


class SitioClearanceSystem:
    def __init__(self):
        self.db = DatabaseManager()
        self.current_user = None
        self.running = True
        
        print("\n" + "="*60)
        print("   SITIO CLEARANCE TICKET SERVICE - Barangay Lapasan")
        print("   Version 2.0")
        print("="*60)
        
        self.login()
    
    def login(self):
        """Handle user login"""
        print("\n" + "-"*40)
        print("   LOGIN")
        print("-"*40)
        
        while self.running and not self.current_user:
            username = input("Username: ").strip()
            password = input("Password: ").strip()
            
            if not username or not password:
                print("❌ Please enter both username and password.")
                continue
            
            user = self.db.authenticate_user(username, password)
            
            if user:
                self.current_user = user
                self.db.log_activity(user['id'], "Login", f"User {user['full_name']} logged in")
                print(f"\n✅ Welcome, {user['full_name']}!")
                print(f"   Role: {user['role'].upper()} | Sitio: {user.get('sitio', 'N/A')}")
                self.main_menu()
            else:
                print("❌ Invalid username or password. Please try again.")
    
    def main_menu(self):
        """Display the main menu based on user role"""
        while self.running and self.current_user:
            print("\n" + "="*50)
            print(f"   MAIN MENU - {self.current_user['full_name']}")
            print("="*50)
            
            role = self.current_user['role']
            menu_options = []
            
            # Common options for all users
            menu_options = [
                ("1", "Dashboard", self.show_dashboard),
                ("2", "New Request", self.show_request_form),
                ("3", "My Clearances", self.show_my_clearances),
                ("4", "Notifications", self.show_notifications),
            ]
            
            # Role-specific options
            if role in ['sitio_leader', 'barangay_officer', 'admin']:
                menu_options.extend([
                    ("5", "All Clearances", self.show_all_clearances),
                    ("6", "Approve Requests", self.show_approval_panel),
                ])
            
            if role in ['barangay_officer', 'admin']:
                menu_options.extend([
                    ("7", "Statistics", self.show_statistics),
                    ("8", "Reports", self.show_reports),
                    ("9", "Settings", self.show_settings),
                ])
            
            if role == 'admin':
                menu_options.append(("10", "User Management", self.show_user_management))
            
            menu_options.append(("0", "Logout", self.logout))
            
            # Display menu
            for num, label, _ in menu_options:
                print(f"  {num}. {label}")
            
            choice = input("\nSelect option: ").strip()
            
            # Find and execute the selected function
            selected = None
            for num, label, func in menu_options:
                if choice == num:
                    selected = func
                    break
            
            if selected:
                selected()
            else:
                print("❌ Invalid option. Please try again.")
    
    def show_dashboard(self):
        """Display dashboard with statistics"""
        print("\n" + "="*50)
        print("   DASHBOARD")
        print("="*50)
        
        user_sitio = self.current_user.get('sitio')
        if self.current_user['role'] == 'resident':
            user_clearances = self.db.get_user_clearances(self.current_user['id'])
            stats = {
                'total': len(user_clearances),
                'pending': len([c for c in user_clearances if c['status'] == 'Pending']),
                'approved': len([c for c in user_clearances if c['status'] == 'Approved']),
                'rejected': len([c for c in user_clearances if c['status'] == 'Rejected']),
                'released': len([c for c in user_clearances if c['status'] == 'Released']),
                'under_review': len([c for c in user_clearances if c['status'] == 'Under Review'])
            }
        elif self.current_user['role'] == 'sitio_leader':
            stats = self.db.get_statistics(sitio_filter=user_sitio)
        else:
            stats = self.db.get_statistics()
        
        if stats:
            print(f"\n📊 Statistics:")
            print("-"*40)
            print(f"  Total Requests:     {stats.get('total', 0)}")
            print(f"  Pending:            {stats.get('pending', 0)}")
            print(f"  Under Review:       {stats.get('under_review', 0)}")
            print(f"  Approved:           {stats.get('approved', 0)}")
            print(f"  Released:           {stats.get('released', 0)}")
            print(f"  Rejected:           {stats.get('rejected', 0)}")
            
            if self.current_user['role'] == 'sitio_leader':
                print(f"\n📍 Managing requests for Sitio: {self.current_user.get('sitio', 'N/A')}")
        
        input("\nPress Enter to continue...")
    
    def get_date_input(self, prompt: str = "Date (YYYY-MM-DD)") -> str:
        """Get date input from user"""
        while True:
            date_str = input(f"{prompt}: ").strip()
            if not date_str:
                return None
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
                return date_str
            except ValueError:
                print("❌ Invalid date format. Please use YYYY-MM-DD.")
    
    def show_request_form(self):
        """Display and process new clearance request"""
        print("\n" + "="*50)
        print("   NEW CLEARANCE REQUEST")
        print("="*50)
        
        print("\n📝 Please provide the following information:")
        print("-"*40)
        
        name = input("Full Name: ").strip()
        if not name:
            print("❌ Name is required.")
            return
        
        address = input("Address: ").strip()
        if not address:
            print("❌ Address is required.")
            return
        
        contact = input("Contact Number: ").strip()
        if not contact:
            print("❌ Contact number is required.")
            return
        
        # Sitio selection
        print("\nSelect Sitio:")
        for i, sitio in enumerate(SITIO_LIST, 1):
            print(f"  {i}. {sitio}")
        
        # Pre-select user's sitio
        default_sitio = None
        if self.current_user['role'] in ['resident', 'sitio_leader'] and self.current_user.get('sitio'):
            default_sitio = self.current_user['sitio']
        
        while True:
            if default_sitio:
                print(f"  (Press Enter to select: {default_sitio})")
            choice = input("Enter sitio number: ").strip()
            
            if not choice and default_sitio:
                sitio = default_sitio
                break
            elif choice.isdigit() and 1 <= int(choice) <= len(SITIO_LIST):
                sitio = SITIO_LIST[int(choice) - 1]
                break
            else:
                print("❌ Invalid selection. Please enter a number between 1 and 4.")
        
        # Purpose selection
        print("\nSelect Purpose:")
        for i, purpose in enumerate(PURPOSE_OPTIONS, 1):
            print(f"  {i}. {purpose}")
        
        while True:
            choice = input("Enter purpose number: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(PURPOSE_OPTIONS):
                purpose = PURPOSE_OPTIONS[int(choice) - 1]
                break
            else:
                print("❌ Invalid selection. Please enter a valid number.")
        
        date_needed = self.get_date_input("Date Needed (YYYY-MM-DD)")
        if not date_needed:
            print("❌ Date needed is required.")
            return
        
        id_number = input("Valid ID Number: ").strip()
        if not id_number:
            print("❌ ID number is required.")
            return
        
        remarks = input("Remarks/Notes (optional): ").strip()
        
        # Generate clearance number
        clearance_number = f"CL-{datetime.now().strftime('%Y%m')}-{''.join(random.choices(string.digits, k=4))}"
        
        clearance_data = {
            "clearance_number": clearance_number,
            "user_id": self.current_user['id'],
            "name": name,
            "address": address,
            "contact": contact,
            "sitio": sitio,
            "purpose": purpose,
            "date_needed": date_needed,
            "id_number": id_number,
            "remarks": remarks,
            "status": "Pending"
        }
        
        try:
            clearance_id = self.db.save_clearance(clearance_data)
            
            self.db.log_activity(
                self.current_user['id'],
                "Submit Clearance",
                f"Submitted clearance {clearance_number} for Sitio {sitio}"
            )
            
            # Notify admin
            self.db.add_notification(
                1,
                "New Clearance Request",
                f"New clearance request from {name} (Sitio: {sitio})"
            )
            
            # Notify sitio leader
            sitio_leaders = self.db.get_users_by_sitio(sitio)
            for leader in sitio_leaders:
                if leader['role'] == 'sitio_leader':
                    self.db.add_notification(
                        leader['id'],
                        f"New Request in {sitio}",
                        f"New clearance request from {name} in your sitio"
                    )
            
            print(f"\n✅ Clearance Request Submitted Successfully!")
            print(f"   Clearance Number: {clearance_number}")
            print(f"   Name: {name}")
            print(f"   Sitio: {sitio}")
            print(f"   Purpose: {purpose}")
            print(f"   Date Needed: {date_needed}")
            print("\n   Please wait for confirmation from the Sitio Leader.")
            
        except Exception as e:
            print(f"❌ Error submitting clearance: {str(e)}")
            logging.error(f"Submission error: {e}")
        
        input("\nPress Enter to continue...")
    
    def show_my_clearances(self):
        """Display user's clearances"""
        print("\n" + "="*50)
        print("   MY CLEARANCE REQUESTS")
        print("="*50)
        
        clearances = self.db.get_user_clearances(self.current_user['id'])
        
        if clearances:
            print("\n📋 Your Clearance Requests:")
            print("-"*80)
            print(f"{'Clearance No.':<15} {'Sitio':<12} {'Purpose':<25} {'Status':<12} {'Date':<12}")
            print("-"*80)
            
            for clearance in clearances:
                status_display = f"● {clearance['status']}"
                date_display = clearance['date_requested'][:10] if clearance['date_requested'] else ''
                print(f"{clearance['clearance_number']:<15} {clearance['sitio']:<12} {clearance['purpose'][:24]:<25} {status_display:<12} {date_display:<12}")
            
            print("-"*80)
            print(f"Total: {len(clearances)} requests")
            
            # Option to generate PDF
            if PDFGenerator.check_reportlab():
                print("\n📄 To generate a PDF certificate, use the 'Generate PDF' option from the Reports menu.")
            else:
                print("\n⚠️ PDF generation unavailable. Install reportlab: pip install reportlab")
        else:
            print("\n📭 You haven't submitted any clearance requests yet.")
        
        input("\nPress Enter to continue...")
    
    def show_all_clearances(self):
        """Display all clearances (for officers)"""
        if self.current_user['role'] not in ['sitio_leader', 'barangay_officer', 'admin']:
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   ALL CLEARANCE REQUESTS")
        print("="*50)
        
        if self.current_user['role'] == 'sitio_leader':
            clearances = self.db.get_all_clearances({'sitio': self.current_user['sitio']})
        else:
            clearances = self.db.get_all_clearances()
        
        if clearances:
            print(f"\n📑 All Clearance Requests ({len(clearances)} total):")
            print("-"*107)
            print(f"{'ID':<5} {'Clearance No.':<15} {'Name':<20} {'Sitio':<12} {'Purpose':<25} {'Status':<12} {'Date':<12}")
            print("-"*107)
            
            for clearance in clearances:
                status_display = f"● {clearance['status'][12]}"
                date_display = clearance['date_requested'][:10] if clearance['date_requested'] else ''
                print(f"{clearance['id']:<5} {clearance['clearance_number']:<15} {clearance['name'][:19]:<20} {clearance['sitio']:<12} {clearance['purpose'][:24]:<25} {status_display:<12} {date_display:<12}")
            
            print("-"*107)
        else:
            print("\n📭 No clearance requests found.")
        
        input("\nPress Enter to continue...")
    
    def show_approval_panel(self):
        """Display pending clearances for approval"""
        if self.current_user['role'] not in ['sitio_leader', 'barangay_officer', 'admin']:
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   APPROVE CLEARANCE REQUESTS")
        print("="*50)
        
        # Get pending clearances
        if self.current_user['role'] == 'sitio_leader':
            pending_clearances = self.db.get_all_clearances({
                'status': 'Pending',
                'sitio': self.current_user['sitio']
            })
        else:
            pending_clearances = self.db.get_all_clearances({'status': 'Pending'})
        
        if pending_clearances:
            print(f"\n📋 Pending Requests ({len(pending_clearances)}):")
            print("-"*80)
            print(f"{'ID':<5} {'Clearance No.':<15} {'Name':<20} {'Sitio':<12} {'Purpose':<25}")
            print("-"*80)
            
            for clearance in pending_clearances:
                print(f"{clearance['id']:<5} {clearance['clearance_number']:<15} {clearance['name'][:19]:<20} {clearance['sitio']:<12} {clearance['purpose'][:24]:<25}")
            
            print("-"*80)
            
            while True:
                print("\nOptions:")
                print("  1. Approve a clearance")
                print("  2. Reject a clearance")
                print("  3. Return to menu")
                
                choice = input("Select option: ").strip()
                
                if choice == "1":
                    self.approve_clearance(pending_clearances)
                    break
                elif choice == "2":
                    self.reject_clearance(pending_clearances)
                    break
                elif choice == "3":
                    break
                else:
                    print("❌ Invalid option.")
        else:
            print("\n✅ No pending clearance requests.")
            input("\nPress Enter to continue...")
    
    def approve_clearance(self, pending_clearances):
        """Approve a clearance request"""
        try:
            clearance_id = input("Enter the ID of the clearance to approve: ").strip()
            if not clearance_id.isdigit():
                print("❌ Invalid ID.")
                return
            
            clearance_id = int(clearance_id)
            
            # Find the clearance in the list
            clearance = None
            for c in pending_clearances:
                if c['id'] == clearance_id:
                    clearance = c
                    break
            
            if not clearance:
                print("❌ Clearance not found.")
                return
            
            confirm = input(f"Approve clearance {clearance['clearance_number']} for {clearance['name']}? (y/n): ").strip().lower()
            if confirm != 'y':
                return
            
            self.db.update_clearance_status(
                clearance_id,
                "Approved",
                approved_by=self.current_user['id']
            )
            
            # Notify user
            self.db.add_notification(
                clearance['user_id'],
                "Clearance Approved",
                f"Your clearance {clearance['clearance_number']} has been approved."
            )
            
            # Generate PDF if available
            if PDFGenerator.check_reportlab():
                pdf_path = PDFGenerator.generate_clearance_certificate(clearance)
                if pdf_path:
                    print(f"   📄 PDF certificate generated: {pdf_path}")
            
            self.db.log_activity(
                self.current_user['id'],
                "Approve Clearance",
                f"Approved clearance ID: {clearance_id}"
            )
            
            print(f"✅ Clearance {clearance['clearance_number']} approved successfully!")
            
        except Exception as e:
            print(f"❌ Error: {str(e)}")
        
        input("\nPress Enter to continue...")
    
    def reject_clearance(self, pending_clearances):
        """Reject a clearance request"""
        try:
            clearance_id = input("Enter the ID of the clearance to reject: ").strip()
            if not clearance_id.isdigit():
                print("❌ Invalid ID.")
                return
            
            clearance_id = int(clearance_id)
            
            # Find the clearance in the list
            clearance = None
            for c in pending_clearances:
                if c['id'] == clearance_id:
                    clearance = c
                    break
            
            if not clearance:
                print("❌ Clearance not found.")
                return
            
            reason = input("Enter reason for rejection: ").strip()
            if not reason:
                print("❌ Rejection reason is required.")
                return
            
            confirm = input(f"Reject clearance {clearance['clearance_number']} for {clearance['name']}? (y/n): ").strip().lower()
            if confirm != 'y':
                return
            
            self.db.update_clearance_status(
                clearance_id,
                "Rejected",
                rejection_reason=reason
            )
            
            # Notify user
            self.db.add_notification(
                clearance['user_id'],
                "Clearance Rejected",
                f"Your clearance {clearance['clearance_number']} was rejected. Reason: {reason}"
            )
            
            self.db.log_activity(
                self.current_user['id'],
                "Reject Clearance",
                f"Rejected clearance ID: {clearance_id}"
            )
            
            print(f"✅ Clearance {clearance['clearance_number']} rejected successfully.")
            
        except Exception as e:
            print(f"❌ Error: {str(e)}")
        
        input("\nPress Enter to continue...")
    
    def show_notifications(self):
        """Display user notifications"""
        print("\n" + "="*50)
        print("   NOTIFICATIONS")
        print("="*50)
        
        notifications = self.db.get_notifications(self.current_user['id'])
        
        if notifications:
            print("\n🔔 Your Notifications:")
            print("-"*60)
            
            for notification in notifications:
                status = "📖" if notification['is_read'] else "🔴"
                print(f"{status} [{notification['created_at']}] {notification['title']}")
                print(f"   {notification['message']}")
                print("-"*60)
            
            print(f"\nTotal: {len(notifications)} notifications")
            
            # Mark all as read option
            mark_all = input("\nMark all as read? (y/n): ").strip().lower()
            if mark_all == 'y':
                for n in notifications:
                    if not n['is_read']:
                        self.db.mark_notification_read(n['id'])
                print("✅ All notifications marked as read.")
        else:
            print("\n📭 No notifications.")
        
        input("\nPress Enter to continue...")
    
    def show_statistics(self):
        """Display statistics (for officers)"""
        if self.current_user['role'] not in ['barangay_officer', 'admin']:
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   STATISTICS DASHBOARD")
        print("="*50)
        
        stats = self.db.get_statistics()
        
        if stats:
            print(f"\n📊 System Statistics:")
            print("-"*40)
            print(f"  Total Requests:     {stats.get('total', 0)}")
            print(f"  Pending:            {stats.get('pending', 0)}")
            print(f"  Under Review:       {stats.get('under_review', 0)}")
            print(f"  Approved:           {stats.get('approved', 0)}")
            print(f"  Released:           {stats.get('released', 0)}")
            print(f"  Rejected:           {stats.get('rejected', 0)}")
            
            if stats.get('sitio_distribution'):
                print("\n🏘️ Sitio Distribution:")
                for item in stats['sitio_distribution']:
                    print(f"  • {item['sitio']}: {item['count']} requests")
            
            if stats.get('monthly_trend'):
                print("\n📈 Monthly Trend (Last 6 months):")
                for item in reversed(stats['monthly_trend']):
                    print(f"  • {item['month']}: {item['count']} requests")
        
        input("\nPress Enter to continue...")
    
    def show_reports(self):
        """Display reports menu (for officers)"""
        if self.current_user['role'] not in ['barangay_officer', 'admin']:
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   REPORTS & EXPORT")
        print("="*50)
        
        print("\n📄 Report Options:")
        print("  1. Generate Monthly Report (PDF)")
        print("  2. Generate Yearly Report (PDF)")
        print("  3. Generate Custom Report (PDF)")
        print("  4. Export All Clearances (JSON)")
        print("  5. Export Pending Requests (JSON)")
        print("  6. Return to menu")
        
        choice = input("\nSelect option: ").strip()
        
        if choice == "1":
            self.generate_monthly_report()
        elif choice == "2":
            self.generate_yearly_report()
        elif choice == "3":
            self.generate_custom_report()
        elif choice == "4":
            self.export_clearances_json()
        elif choice == "5":
            self.export_pending_json()
        elif choice == "6":
            return
        else:
            print("❌ Invalid option.")
        
        input("\nPress Enter to continue...")
    
    def generate_monthly_report(self):
        """Generate monthly report"""
        try:
            if not PDFGenerator.check_reportlab():
                print("❌ PDF Generation Error: reportlab library is not installed.")
                print("   Please install it using: pip install reportlab")
                return
            
            current_month = datetime.now().strftime("%Y-%m")
            
            if self.current_user['role'] == 'sitio_leader':
                clearances = self.db.get_all_clearances({'sitio': self.current_user['sitio']})
            else:
                clearances = self.db.get_all_clearances()
            
            monthly_data = [c for c in clearances if c.get('date_requested', '').startswith(current_month)]
            
            if monthly_data:
                filename = f"monthly_report_{current_month}.pdf"
                pdf_path = PDFGenerator.generate_clearance_report(
                    monthly_data,
                    report_type="monthly",
                    date_range=f"Month of {datetime.now().strftime('%B %Y')}",
                    output_path=filename
                )
                
                if pdf_path:
                    print(f"✅ Monthly report generated: {pdf_path}")
                else:
                    print("❌ Failed to generate report.")
            else:
                print("📭 No data found for the current month.")
                
        except Exception as e:
            print(f"❌ Error: {str(e)}")
    
    def generate_yearly_report(self):
        """Generate yearly report"""
        try:
            if not PDFGenerator.check_reportlab():
                print("❌ PDF Generation Error: reportlab library is not installed.")
                print("   Please install it using: pip install reportlab")
                return
            
            current_year = datetime.now().strftime("%Y")
            
            if self.current_user['role'] == 'sitio_leader':
                clearances = self.db.get_all_clearances({'sitio': self.current_user['sitio']})
            else:
                clearances = self.db.get_all_clearances()
            
            yearly_data = [c for c in clearances if c.get('date_requested', '').startswith(current_year)]
            
            if yearly_data:
                filename = f"yearly_report_{current_year}.pdf"
                pdf_path = PDFGenerator.generate_clearance_report(
                    yearly_data,
                    report_type="yearly",
                    date_range=f"Year {current_year}",
                    output_path=filename
                )
                
                if pdf_path:
                    print(f"✅ Yearly report generated: {pdf_path}")
                else:
                    print("❌ Failed to generate report.")
            else:
                print("📭 No data found for the current year.")
                
        except Exception as e:
            print(f"❌ Error: {str(e)}")
    
    def generate_custom_report(self):
        """Generate custom report"""
        try:
            if not PDFGenerator.check_reportlab():
                print("❌ PDF Generation Error: reportlab library is not installed.")
                print("   Please install it using: pip install reportlab")
                return
            
            date_from = input("Enter start date (YYYY-MM-DD) or press Enter for all: ").strip()
            date_to = input("Enter end date (YYYY-MM-DD) or press Enter for all: ").strip()
            
            filters = {}
            if date_from:
                filters['date_from'] = date_from
            if date_to:
                filters['date_to'] = date_to
            
            if self.current_user['role'] == 'sitio_leader':
                filters['sitio'] = self.current_user['sitio']
            
            clearances = self.db.get_all_clearances(filters if filters else None)
            
            if clearances:
                filename = f"custom_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                date_range = f"{date_from or 'Start'} to {date_to or 'End'}"
                pdf_path = PDFGenerator.generate_clearance_report(
                    clearances,
                    report_type="custom",
                    date_range=date_range,
                    output_path=filename
                )
                
                if pdf_path:
                    print(f"✅ Custom report generated: {pdf_path}")
                else:
                    print("❌ Failed to generate report.")
            else:
                print("📭 No data found for the specified date range.")
                
        except Exception as e:
            print(f"❌ Error: {str(e)}")
    
    def export_clearances_json(self):
        """Export clearances to JSON"""
        if self.current_user['role'] == 'sitio_leader':
            clearances = self.db.get_all_clearances({'sitio': self.current_user['sitio']})
        else:
            clearances = self.db.get_all_clearances()
        
        if clearances:
            filename = f"clearances_export_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                with open(filename, 'w') as f:
                    json.dump(clearances, f, indent=2, default=str)
                print(f"✅ Clearances exported to {filename}")
            except Exception as e:
                print(f"❌ Export failed: {str(e)}")
        else:
            print("📭 No clearances to export.")
    
    def export_pending_json(self):
        """Export pending requests to JSON"""
        if self.current_user['role'] == 'sitio_leader':
            pending = self.db.get_all_clearances({
                'status': 'Pending',
                'sitio': self.current_user['sitio']
            })
        else:
            pending = self.db.get_all_clearances({'status': 'Pending'})
        
        if pending:
            filename = f"pending_requests_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                with open(filename, 'w') as f:
                    json.dump(pending, f, indent=2, default=str)
                print(f"✅ Pending requests exported to {filename}")
            except Exception as e:
                print(f"❌ Export failed: {str(e)}")
        else:
            print("📭 No pending requests to export.")
    
    def show_settings(self):
        """Display system settings (for officers)"""
        if self.current_user['role'] not in ['barangay_officer', 'admin']:
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   SETTINGS")
        print("="*50)
        
        pdf_installed = "✅ Yes" if PDFGenerator.check_reportlab() else "❌ No"
        
        print(f"\n⚙️ System Information:")
        print("-"*40)
        print(f"  System Name:     Sitio Clearance System")
        print(f"  Version:         2.0")
        print(f"  Database:        SQLite")
        print(f"  Current User:    {self.current_user['full_name']}")
        print(f"  User Role:       {self.current_user['role']}")
        print(f"  Sitio:           {self.current_user.get('sitio', 'N/A')}")
        print(f"  System Status:   Online")
        print(f"  PDF Support:     {pdf_installed}")
        
        print("\n📋 Registered Sitios:")
        for sitio in SITIO_LIST:
            print(f"  • {sitio}")
        
        if not PDFGenerator.check_reportlab():
            print("\n⚠️ PDF Generation is NOT available.")
            print("   To enable PDF generation, install reportlab:")
            print("   pip install reportlab")
        
        input("\nPress Enter to continue...")
    
    def show_user_management(self):
        """Display user management (for admin only)"""
        if self.current_user['role'] != 'admin':
            print("❌ Access Denied. You don't have permission to view this page.")
            input("\nPress Enter to continue...")
            return
        
        print("\n" + "="*50)
        print("   USER MANAGEMENT")
        print("="*50)
        
        print("\n👥 All Users:")
        print("-"*80)
        print(f"{'ID':<5} {'Username':<15} {'Full Name':<20} {'Role':<15} {'Sitio':<12}")
        print("-"*80)
        
        users = self.db.get_all_users()
        for user in users:
            print(f"{user['id']:<5} {user['username']:<15} {user['full_name'][:19]:<20} {user['role']:<15} {user.get('sitio', 'N/A'):<12}")
        
        print("-"*80)
        
        print("\nOptions:")
        print("  1. Create New User")
        print("  2. Return to menu")
        
        choice = input("Select option: ").strip()
        if choice == "1":
            self.create_user_dialog()
        elif choice == "2":
            return
        else:
            print("❌ Invalid option.")
        
        input("\nPress Enter to continue...")
    
    def create_user_dialog(self):
        """Create a new user"""
        if self.current_user['role'] != 'admin':
            print("❌ Access Denied.")
            return
        
        print("\n" + "="*50)
        print("   CREATE NEW USER")
        print("="*50)
        
        username = input("Username: ").strip()
        if not username:
            print("❌ Username is required.")
            return
        
        password = input("Password: ").strip()
        if not password:
            print("❌ Password is required.")
            return
        
        full_name = input("Full Name: ").strip()
        if not full_name:
            print("❌ Full name is required.")
            return
        
        print("\nSelect Role:")
        print("  1. resident")
        print("  2. sitio_leader")
        print("  3. barangay_officer")
        print("  4. admin")
        
        role_choice = input("Enter role number: ").strip()
        role_map = {"1": "resident", "2": "sitio_leader", "3": "barangay_officer", "4": "admin"}
        role = role_map.get(role_choice)
        
        if not role:
            print("❌ Invalid role selection.")
            return
        
        email = input("Email (optional): ").strip() or None
        phone = input("Phone (optional): ").strip() or None
        
        sitio = None
        if role != "admin":
            print("\nSelect Sitio:")
            for i, s in enumerate(SITIO_LIST, 1):
                print(f"  {i}. {s}")
            
            sitio_choice = input("Enter sitio number: ").strip()
            if sitio_choice.isdigit() and 1 <= int(sitio_choice) <= len(SITIO_LIST):
                sitio = SITIO_LIST[int(sitio_choice) - 1]
            else:
                print("❌ Invalid sitio selection.")
                return
        
        try:
            user_id = self.db.create_user(
                username, password, full_name, role, email, phone, sitio
            )
            print(f"✅ User {full_name} created successfully! (ID: {user_id})")
        except sqlite3.IntegrityError:
            print("❌ Username already exists.")
        except Exception as e:
            print(f"❌ Failed to create user: {str(e)}")
    
    def logout(self):
        """Logout the current user"""
        if self.current_user:
            self.db.log_activity(
                self.current_user['id'],
                "Logout",
                f"User {self.current_user['full_name']} logged out"
            )
            print(f"\n👋 Goodbye, {self.current_user['full_name']}!")
            self.current_user = None
        
        self.running = False
        print("\n" + "="*50)
        print("   Thank you for using Sitio Clearance System")
        print("="*50)


def main():
    """Main entry point"""
    try:
        app = SitioClearanceSystem()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ An error occurred: {str(e)}")
        logging.error(f"Application error: {e}")


if __name__ == "__main__":
    main()