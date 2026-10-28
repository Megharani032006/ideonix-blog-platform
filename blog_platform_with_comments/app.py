import os
import re
import secrets
import sqlite3
import uuid
import warnings
from io import BytesIO
from collections import Counter
from contextlib import contextmanager
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from PIL import Image, UnidentifiedImageError
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
DB_PATH = os.path.join(os.path.dirname(__file__), "blog.db")
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
CATEGORIES = (
    "General",
    "Technology",
    "AI & Data",
    "Science & Research",
    "Education",
    "Career",
    "Business & Startups",
    "Finance",
    "Environment",
    "Health & Wellness",
    "Lifestyle",
    "Personal Development",
    "Arts & Culture",
    "Books & Literature",
    "Food & Cooking",
    "Travel",
    "Sports",
    "History",
    "News & Society",
    "Kannada Stories",
)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_column_if_missing(db, table, column, definition):
    names = [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in names:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def available_categories(db):
    existing = [row[0] for row in db.execute(
        "SELECT DISTINCT category FROM posts WHERE category IS NOT NULL ORDER BY category"
    ).fetchall()]
    return list(dict.fromkeys((*CATEGORIES, *existing)))


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        # Migrate existing databases safely; old accounts and posts are preserved.
        add_column_if_missing(db, "posts", "category", "TEXT NOT NULL DEFAULT 'General'")
        add_column_if_missing(db, "posts", "tags", "TEXT NOT NULL DEFAULT ''")
        add_column_if_missing(db, "posts", "language", "TEXT NOT NULL DEFAULT 'English'")
        add_column_if_missing(db, "posts", "image_path", "TEXT")
        add_column_if_missing(db, "comments", "parent_id", "INTEGER REFERENCES comments(id) ON DELETE CASCADE")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS post_likes (
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, post_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS bookmarks (
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(user_id, post_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
        """)


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]

app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not sent or not expected or not secrets.compare_digest(sent, expected):
            abort(400, description="Invalid or missing form security token. Refresh the page and try again.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to do that.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def post_fields():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    category = request.form.get("category", "General").strip()[:40] or "General"
    language = request.form.get("language", "English").strip()
    if language not in ("English", "Kannada", "Both"):
        language = "English"
    raw_tags = request.form.get("tags", "")
    tags = ", ".join(dict.fromkeys(t.strip().lower()[:24] for t in raw_tags.split(",") if t.strip()))[:250]
    return title, content, category, tags, language


def read_featured_image(upload):
    if not upload or not upload.filename:
        return None, None, None

    extension = os.path.splitext(upload.filename)[1].lower()
    expected_format = IMAGE_FORMATS.get(extension)
    if not expected_format:
        return None, None, "Choose a JPG, JPEG, PNG, or WEBP image."

    image_data = upload.stream.read(MAX_IMAGE_SIZE + 1)
    if len(image_data) > MAX_IMAGE_SIZE:
        return None, None, "Featured images must be 5 MB or smaller."

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_data)) as image:
                if image.format != expected_format:
                    return None, None, "The selected file extension does not match its image format."
                image.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning,
            OSError, UnidentifiedImageError, ValueError):
        return None, None, "The selected file is not a valid supported image."

    return image_data, extension, None


def save_featured_image(image_data, extension):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{extension}"
    with open(os.path.join(UPLOAD_FOLDER, filename), "xb") as image_file:
        image_file.write(image_data)
    return f"uploads/{filename}"


def remove_featured_image(image_path):
    if not image_path or not re.fullmatch(r"uploads/[a-f0-9]{32}\.(jpg|jpeg|png|webp)", image_path):
        return
    path = os.path.join(UPLOAD_FOLDER, os.path.basename(image_path))
    if os.path.isfile(path):
        os.remove(path)


@app.route("/")
def index():
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "").strip()[:40]
    language = request.args.get("language", "").strip()
    sql = """SELECT p.*, u.username,
        (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
        (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS like_count
        FROM posts p JOIN users u ON u.id=p.user_id WHERE 1=1"""
    args = []
    if q:
        sql += " AND (p.title LIKE ? OR p.content LIKE ? OR p.tags LIKE ? OR p.category LIKE ?)"
        args += [f"%{q}%"] * 4
    if category:
        sql += " AND p.category = ?"; args.append(category)
    if language in ("English", "Kannada", "Both"):
        sql += " AND p.language = ?"; args.append(language)
    sql += " ORDER BY p.id DESC"
    with get_db() as db:
        posts = db.execute(sql, args).fetchall()
        categories = available_categories(db)
        bookmarked_ids = set()
        liked_ids = set()
        if session.get("user_id"):
            uid = session["user_id"]
            bookmarked_ids = {r[0] for r in db.execute("SELECT post_id FROM bookmarks WHERE user_id=?", (uid,)).fetchall()}
            liked_ids = {r[0] for r in db.execute("SELECT post_id FROM post_likes WHERE user_id=?", (uid,)).fetchall()}
        recommendations = []
        if session.get("user_id"):
            # Lightweight, explainable recommendations based on saved/liked topics.
            signals = db.execute("""SELECT p.category, p.tags FROM posts p
                JOIN (SELECT post_id FROM bookmarks WHERE user_id=? UNION SELECT post_id FROM post_likes WHERE user_id=?) x ON x.post_id=p.id""",
                (session["user_id"], session["user_id"])).fetchall()
            interests = Counter()
            for signal in signals:
                interests[signal["category"].lower()] += 2
                for tag in signal["tags"].split(","):
                    if tag.strip(): interests[tag.strip().lower()] += 1
            if interests:
                candidates = db.execute("""SELECT p.*, u.username,
                    (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
                    (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS like_count
                    FROM posts p JOIN users u ON u.id=p.user_id WHERE p.user_id != ? ORDER BY p.id DESC LIMIT 100""",
                    (session["user_id"],)).fetchall()
                ranked = []
                for p in candidates:
                    score = interests[p["category"].lower()] * 2
                    score += sum(interests[t.strip().lower()] for t in p["tags"].split(",") if t.strip())
                    if score > 0: ranked.append((score, p))
                recommendations = [p for _, p in sorted(ranked, key=lambda item: (item[0], item[1]["id"]), reverse=True)[:3]]
    return render_template("index.html", posts=posts, q=q, category=category, language=language,
                           categories=categories, bookmarked_ids=bookmarked_ids, liked_ids=liked_ids,
                           recommendations=recommendations)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(username) > 30:
            flash("Username must be between 3 and 30 characters.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        else:
            try:
                with get_db() as db:
                    db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                               (username, generate_password_hash(password)))
                flash("Account created! You can now log in.", "success")
                return redirect(url_for("login"))
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear(); session["user_id"] = user["id"]; session["username"] = user["username"]
            session["_csrf_token"] = secrets.token_urlsafe(32)
            flash("Welcome back!", "success")
            return redirect(url_for("index"))
        flash("Incorrect username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear(); flash("You have logged out.", "info")
    return redirect(url_for("index"))


@app.route("/post/new", methods=["GET", "POST"])
@login_required
def new_post():
    with get_db() as db:
        categories = available_categories(db)
    if request.method == "POST":
        title, content, category, tags, language = post_fields()
        image_data, image_extension, image_error = read_featured_image(request.files.get("featured_image"))
        form_post = {"title": title, "content": content, "category": category, "tags": tags, "language": language}
        if not title:
            flash("Please enter a title for your post.", "error")
        elif not content:
            flash("Please enter the post content.", "error")
        elif len(title) > 150:
            flash("Title must be 150 characters or fewer.", "error")
        elif image_error:
            flash(image_error, "error")
        else:
            image_path = save_featured_image(image_data, image_extension) if image_data else None
            with get_db() as db:
                db.execute("""INSERT INTO posts (user_id,title,content,category,tags,language,image_path)
                    VALUES (?,?,?,?,?,?,?)""",
                           (session["user_id"], title, content, category, tags, language, image_path))
            flash("Your blog post was published.", "success")
            return redirect(url_for("index"))
        return render_template("post_form.html", post=form_post, heading="Write a post", categories=categories)
    return render_template("post_form.html", post=None, heading="Write a post", categories=categories)


@app.route("/post/<int:post_id>")
def post_detail(post_id):
    with get_db() as db:
        post = db.execute("""SELECT p.*,u.username,
            (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS like_count
            FROM posts p JOIN users u ON u.id=p.user_id WHERE p.id=?""", (post_id,)).fetchone()
        if post is None: abort(404)
        comments = db.execute("""SELECT c.*,u.username,
            (SELECT COUNT(*) FROM comments replies WHERE replies.parent_id=c.id) AS reply_count
            FROM comments c JOIN users u ON u.id=c.user_id WHERE c.post_id=? ORDER BY c.id ASC""", (post_id,)).fetchall()
        liked = bool(session.get("user_id") and db.execute("SELECT 1 FROM post_likes WHERE user_id=? AND post_id=?", (session["user_id"], post_id)).fetchone())
        bookmarked = bool(session.get("user_id") and db.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND post_id=?", (session["user_id"], post_id)).fetchone())
        related = db.execute("""SELECT p.id,p.title,p.category FROM posts p WHERE p.id != ? AND
            (p.category=? OR (p.tags != '' AND p.tags LIKE ?)) ORDER BY p.id DESC LIMIT 3""",
            (post_id, post["category"], f"%{post['tags'].split(',')[0].strip()}%" if post["tags"].strip() else "%unlikely%" )).fetchall()
    return render_template("post_detail.html", post=post, comments=comments, liked=liked,
                           bookmarked=bookmarked, related=related)


@app.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    with get_db() as db:
        categories = available_categories(db)
        post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if post is None: abort(404)
        if post["user_id"] != session["user_id"]: abort(403)
        if request.method == "POST":
            title, content, category, tags, language = post_fields()
            image_data, image_extension, image_error = read_featured_image(request.files.get("featured_image"))
            form_post = dict(post)
            form_post.update(title=title, content=content, category=category, tags=tags, language=language)
            if not title:
                flash("Please enter a title for your post.", "error")
            elif not content:
                flash("Please enter the post content.", "error")
            elif len(title) > 150:
                flash("Title must be 150 characters or fewer.", "error")
            elif image_error:
                flash(image_error, "error")
            else:
                previous_image = post["image_path"]
                image_path = save_featured_image(image_data, image_extension) if image_data else previous_image
                if request.form.get("remove_image") == "1" and not image_data:
                    image_path = None
                db.execute("""UPDATE posts SET title=?,content=?,category=?,tags=?,language=?,image_path=?
                    WHERE id=?""",
                           (title, content, category, tags, language, image_path, post_id))
                db.commit()
                if previous_image and previous_image != image_path:
                    remove_featured_image(previous_image)
                flash("Post updated.", "success")
                return redirect(url_for("post_detail", post_id=post_id))
            return render_template("post_form.html", post=form_post, heading="Edit your post", categories=categories)
    return render_template("post_form.html", post=post, heading="Edit your post", categories=categories)


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    with get_db() as db:
        post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if post is None: abort(404)
        if post["user_id"] != session["user_id"]: abort(403)
        db.execute("DELETE FROM posts WHERE id=?", (post_id,))
        image_path = post["image_path"]
    remove_featured_image(image_path)
    flash("Post deleted.", "success")
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def add_comment(post_id):
    content = request.form.get("content", "").strip()
    parent_id = request.form.get("parent_id", type=int)
    with get_db() as db:
        if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone(): abort(404)
        if parent_id and not db.execute("SELECT id FROM comments WHERE id=? AND post_id=? AND parent_id IS NULL", (parent_id, post_id)).fetchone():
            abort(400, description="The comment you are replying to was not found.")
        if not content: flash("Comment cannot be empty.", "error")
        elif len(content) > 1000: flash("Comment must be 1,000 characters or fewer.", "error")
        else:
            db.execute("INSERT INTO comments (post_id,user_id,content,parent_id) VALUES (?,?,?,?)",
                       (post_id, session["user_id"], content, parent_id))
            flash("Reply added." if parent_id else "Comment added.", "success")
    return redirect(url_for("post_detail", post_id=post_id) + (f"#comment-{parent_id}" if parent_id else "#comments"))


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    with get_db() as db:
        if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone(): abort(404)
        exists = db.execute("SELECT 1 FROM post_likes WHERE user_id=? AND post_id=?", (session["user_id"], post_id)).fetchone()
        if exists:
            db.execute("DELETE FROM post_likes WHERE user_id=? AND post_id=?", (session["user_id"], post_id))
        else:
            db.execute("INSERT INTO post_likes (user_id,post_id) VALUES (?,?)", (session["user_id"], post_id))
    return redirect(request.form.get("next") or url_for("post_detail", post_id=post_id))


@app.route("/post/<int:post_id>/bookmark", methods=["POST"])
@login_required
def toggle_bookmark(post_id):
    with get_db() as db:
        if not db.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone(): abort(404)
        exists = db.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND post_id=?", (session["user_id"], post_id)).fetchone()
        if exists:
            db.execute("DELETE FROM bookmarks WHERE user_id=? AND post_id=?", (session["user_id"], post_id))
            flash("Removed from your reading list.", "info")
        else:
            db.execute("INSERT INTO bookmarks (user_id,post_id) VALUES (?,?)", (session["user_id"], post_id))
            flash("Saved to your reading list.", "success")
    return redirect(request.form.get("next") or url_for("post_detail", post_id=post_id))


@app.route("/bookmarks")
@login_required
def my_bookmarks():
    with get_db() as db:
        posts = db.execute("""SELECT p.*,u.username,
            (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
            (SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) AS like_count
            FROM bookmarks b JOIN posts p ON p.id=b.post_id JOIN users u ON u.id=p.user_id
            WHERE b.user_id=? ORDER BY b.created_at DESC""", (session["user_id"],)).fetchall()
        bookmarked_ids = {p["id"] for p in posts}
        liked_ids = {r[0] for r in db.execute("SELECT post_id FROM post_likes WHERE user_id=?", (session["user_id"],)).fetchall()}
    return render_template("bookmarks.html", posts=posts, bookmarked_ids=bookmarked_ids, liked_ids=liked_ids)


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request")), 400

@app.errorhandler(403)
def forbidden(error):
    return render_template("error.html", code=403, message="You do not have permission to do that."), 403

@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="The page or post could not be found."), 404


@app.errorhandler(413)
def request_too_large(error):
    return render_template("error.html", code=413,
                           message="The upload is too large. Featured images must be 5 MB or smaller."), 413


@app.errorhandler(500)
def internal_server_error(error):
    return render_template("error.html", code=500,
                           message="Something went wrong on our end. Please try again later."), 500


init_db()

if __name__ == "__main__":
    app.run(debug=True)
