# Rent It Bro - Student Resource Exchange Platform

**BCA Mini Project — Phase 1: Authentication and Profile Management**

---

## 1. Project Overview

**Rent It Bro** is a campus-centric student marketplace platform designed to help college students buy, sell, rent, or exchange academic and campus essentials — including textbooks, electronics, laptops, tablets, cycles, lab gear, sports equipment, and hostel items — directly with verified college peers.

### Phase 1 Objective
Phase 1 focuses on building a secure, clean, and scalable architectural foundation consisting of:
1. Modular Flask web application setup.
2. Structured MySQL database schema and parameterized queries via `mysql-connector-python`.
3. Student registration with input validation and duplicate prevention.
4. Secure login and logout powered by Werkzeug password hashing (scrypt/PBKDF2).
5. Server-side session management with protected route decorators.
6. Student dashboard showcasing academic credentials, campus location, and trust status.
7. Student profile management allowing students to view and update their information.
8. Responsive, clean UI built with Bootstrap 5 and custom CSS.

> **Note on Scope**: Marketplace listings, rent/exchange transactions, peer ratings, and AI features are scheduled for later phases (Phase 2 through Phase 4) and are intentionally not included in Phase 1.

---

## 2. Technology Stack

| Component | Technology | Description |
|---|---|---|
| **Backend** | Python 3.8+ / Flask | Lightweight, beginner-friendly WSGI framework |
| **Database** | MySQL (with SQLite local fallback) | Relational database for persistent user records |
| **Connector** | `mysql-connector-python` | Official MySQL driver for Python |
| **Security** | Werkzeug Security | Industry-standard password hashing and verification |
| **Sessions** | Flask Sessions | Secure cookie-based server session tracking |
| **Configuration** | `python-dotenv` | Environment variable management via `.env` |
| **Frontend** | HTML5, CSS3, Bootstrap 5, JS | Responsive, modern student-oriented interface |

---

## 3. Project Structure

```text
student_resource_exchange/
│
├── app.py                     # Main Flask application with routes & DB helpers
├── config.py                  # Configuration loader using python-dotenv
├── requirements.txt           # Python package dependencies
├── .env                       # Local environment secrets (ignored by Git)
├── .env.example               # Example environment variable template
├── .gitignore                 # Files excluded from version control
│
├── database/
│   └── schema.sql             # SQL script for MySQL database & users table
│
├── templates/                 # Jinja2 HTML templates
│   ├── base.html              # Base layout with navbar, alerts & footer
│   ├── index.html             # Landing page with project overview
│   ├── login.html             # Student sign-in form
│   ├── register.html          # Student registration form
│   ├── dashboard.html         # Protected student dashboard
│   └── profile.html           # Protected profile view & update page
│
├── static/                    # Static assets
│   ├── css/
│   │   └── style.css          # Custom styling & responsive design rules
│   ├── js/
│   │   └── script.js          # Client-side form validation & alert dismissal
│   └── images/                # Placeholder for profile photos & item images
│
└── README.md                  # Comprehensive setup & project documentation
```

---

## 4. Required Software

Before setting up the project, ensure you have the following installed:
1. **Python 3.8 or higher**: [Download Python](https://www.python.org/downloads/) (Check *"Add python.exe to PATH"* during installation).
2. **MySQL Server / XAMPP / WampServer**: For local MySQL hosting.
3. **Visual Studio Code (VS Code)** or any code editor of your choice.
4. **Modern Web Browser**: Google Chrome, Mozilla Firefox, or Microsoft Edge.

---

## 5. Installation & Setup Guide (Windows / VS Code)

### Step 1: Open the Project in VS Code
1. Launch **VS Code**.
2. Go to `File` > `Open Folder...` and select the `student_resource_exchange` folder.
3. Open the built-in terminal by pressing ``Ctrl + ` `` (Backtick) or going to `Terminal` > `New Terminal`.

### Step 2: Create a Python Virtual Environment
In the VS Code terminal (PowerShell or Command Prompt):
```powershell
python -m venv venv
```

### Step 3: Activate the Virtual Environment
- On **Windows (PowerShell)**:
  ```powershell
  venv\Scripts\Activate.ps1
  ```
  *(If execution of scripts is disabled, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in PowerShell first).*

- On **Windows (Command Prompt / CMD)**:
  ```cmd
  venv\Scripts\activate.bat
  ```

- On **macOS / Linux**:
  ```bash
  source venv/bin/activate
  ```

Your terminal prompt will now show `(venv)` indicating the virtual environment is active.

### Step 4: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables
Copy `.env.example` to `.env`:
```powershell
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```
Edit `.env` if your MySQL configuration differs from defaults:
```env
# Flask Secret Key
SECRET_KEY=CHANGE_ME_USE_RANDOM_64_CHAR_HEX

# MySQL Database Configuration
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=
DB_NAME=student_exchange
```
*(If you are using XAMPP, `DB_USER` is typically `root` with an empty `DB_PASSWORD`).*

### Step 6: Initialize the MySQL Database
1. Start your **MySQL service** (e.g., click **Start** for MySQL in the XAMPP Control Panel, or start Windows MySQL service).
2. Open MySQL Command Line or phpMyAdmin and execute the SQL file:
   ```bash
   mysql -u root -p < database/schema.sql
   ```
   *Alternatively*, open **phpMyAdmin** (`http://localhost/phpmyadmin`), navigate to the **Import** tab, choose `database/schema.sql`, and click **Go**.

> **Note**: `app.py` also features automatic database and table initialization on startup if MySQL is running.

---

## 6. Running the Application

With your virtual environment activated, run:
```bash
python app.py
```

You will see output similar to:
```text
 * Rent It Bro Phase 1 starting on http://0.0.0.0:3000
 * Serving Flask app 'app'
 * Running on http://127.0.0.1:3000
```

Open your web browser and navigate to:
```text
http://127.0.0.1:3000
```
or
```text
http://localhost:3000
```

---

## 7. Application Routes & Flow

| HTTP Method | Route | Access | Purpose |
|---|---|---|---|
| `GET` | `/` | Public | Landing page explaining Rent It Bro & feature highlights |
| `GET`, `POST` | `/register` | Public | Student account registration with input validation |
| `GET`, `POST` | `/login` | Public | Student authentication using hashed passwords |
| `GET` | `/logout` | Authenticated | Clears user session and redirects to login |
| `GET` | `/dashboard` | Protected (`@login_required`) | Student dashboard showing details & account status |
| `GET`, `POST` | `/profile` | Protected (`@login_required`) | View and edit student academic details & campus location |

---

## 8. Database Schema (Phase 1)

### Table: `users`
| Column | Data Type | Modifiers | Description |
|---|---|---|---|
| `user_id` | `INT` | `AUTO_INCREMENT PRIMARY KEY` | Unique student ID |
| `name` | `VARCHAR(100)` | `NOT NULL` | Student full name |
| `email` | `VARCHAR(150)` | `NOT NULL UNIQUE` | College / personal email |
| `password` | `VARCHAR(255)` | `NOT NULL` | Secure Werkzeug password hash |
| `college` | `VARCHAR(150)` | `NOT NULL` | College / university name |
| `course` | `VARCHAR(100)` | `NOT NULL` | Degree program (e.g. BCA, B.Tech) |
| `year` | `VARCHAR(20)` | `NOT NULL` | Academic year (e.g. 1st Year, 2nd Year) |
| `location` | `VARCHAR(150)` | `NOT NULL` | Campus / hostel location |
| `profile_image` | `VARCHAR(255)` | `DEFAULT NULL` | Avatar path (for future phases) |
| `rating` | `DECIMAL(3, 2)` | `DEFAULT 0.00` | Peer trust rating |
| `created_at` | `TIMESTAMP` | `DEFAULT CURRENT_TIMESTAMP` | Account creation timestamp |
| `status` | `VARCHAR(20)` | `DEFAULT 'active'` | Student account status |

---

## 9. Security Implementations

1. **Password Hashing**: Stored passwords use `werkzeug.security.generate_password_hash` (scrypt / PBKDF2 with unique salt). Plaintext passwords are never stored.
2. **SQL Injection Prevention**: All queries use parameterized inputs (`%s` placeholders passed as tuple parameters). String concatenation is strictly prohibited.
3. **Session Protection**: Flask cookie sessions are encrypted using `SECRET_KEY`.
4. **Access Control**: Protected pages (`/dashboard`, `/profile`) use the `@login_required` decorator to prevent unauthenticated access.
5. **Credential Safety**: No database credentials or secrets are committed to version control; `.env` is listed in `.gitignore`.
6. **Error Masking**: Database connection errors and query exceptions are logged server-side and masked with friendly, safe flash messages.

---

## 10. Viva & Evaluation Questions (BCA Exam Preparation)

1. **Why use Werkzeug for password hashing instead of MD5 or SHA256?**
   - *Answer*: MD5 and plain SHA256 are fast hash functions prone to rainbow table attacks. Werkzeug utilizes salted, computationally heavy algorithms (scrypt/PBKDF2) designed to resist brute-force attacks.

2. **How does session management work in Flask?**
   - *Answer*: Flask signs cookie-based sessions cryptographically using the application's `SECRET_KEY`. When a student logs in, their `user_id` is stored in the session dictionary, allowing the server to identify them on subsequent requests.

3. **What is Jinja2 template inheritance?**
   - *Answer*: Jinja2 template inheritance allows `base.html` to define the shared HTML scaffolding (head, navbar, flash messages, footer), while specific views override the `{% block content %}` block without duplicating code.

4. **Why are environment variables used for database configuration?**
   - *Answer*: Decoupling credentials from source code adheres to 12-factor application design, preventing security leaks if code is pushed to public repositories.

---

## 11. Upcoming Roadmap (Phase 2 & Beyond)

- **Phase 2: Marketplace & Item Listings**
  - Schema tables: `categories`, `items`, `item_images`.
  - Item posting (rent, sell, exchange).
  - Category filters and item search.
- **Phase 3: Exchange Requests & Transactions**
  - Schema tables: `requests`, `transactions`, `reviews`.
  - Rent duration tracking, peer trust ratings, and messaging.
- **Phase 4: AI Campus Assistant**
  - Fair market rental price estimation using Gemini API.
  - Natural language search and campus resource matching.
