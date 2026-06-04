import os
from flask import Flask

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev')


@app.route('/health')
def health():
    return {'status': 'ok'}


@app.route('/')
def index():
    return '<h1>Job Agent Dashboard</h1><p>Full UI arrives in Session 3.</p>'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
