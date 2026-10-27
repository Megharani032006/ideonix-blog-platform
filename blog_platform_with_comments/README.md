# IDEONIX — Ideas That Move the World.

An AI-powered, English and Kannada knowledge-sharing and blogging platform built with Flask, SQLite, HTML, CSS, and JavaScript.

## Features
- User registration, login/logout, secure password hashing and CSRF checks
- Create, edit and delete your own blog posts
- Categories, tags, search and English/Kannada/Both language labels
- Comments and one-level threaded replies
- Like/unlike posts and bookmark/unbookmark posts
- Saved-post reading list
- Explainable recommendations based on categories and tags from posts you like/save
- English/Kannada interface toggle (saved in your browser)
- Free offline writing helper: title ideas, a short extractive summary and writing tips
- Optional JPG, JPEG, PNG, and WEBP post images with server-side validation (maximum 5 MB)
- Responsive featured images on post cards and article pages; replace or remove an image when editing

## About the free writing helper
The helper runs in the browser and does not send your writing to an external service. It uses simple text heuristics, not a large generative AI model, so suggestions are basic and should be reviewed by the author. No API key or paid account is needed.

## Run on Windows
1. Open the `blog_platform_with_comments` folder in VS Code.
2. Open Terminal → New Terminal.
3. If the virtual environment is in the parent folder, activate it with:
   `..\.venv\Scripts\Activate.ps1`
   (If you do not have that environment, you can run the commands below with your normal Python installation.)
4. Install dependencies, including Pillow for image-content validation:
   `python -m pip install -r requirements.txt`
5. Run the app:
   `python app.py`
6. Open `http://127.0.0.1:5000` in your browser.

The SQLite database `blog.db` is next to `app.py`. Startup adds a nullable `image_path` column to the existing posts table when needed; existing accounts and posts are kept. Uploaded files are stored in `static\uploads`. Back up `blog.db` before making major changes.

## Important
- This is a local development project. Before production hosting, set a persistent, private `SECRET_KEY`, disable Flask debug mode, and use a production WSGI server. If `SECRET_KEY` is not set locally, a random key is generated on each start.
- Interface labels can switch between English and Kannada. Users can also publish Kannada content directly; the application does not automatically translate blog posts.
- The included writing helper runs in the browser and uses simple text heuristics; it does not call a paid AI service or send writing to an external service.
