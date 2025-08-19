from flask import (Flask, request, render_template, redirect, url_for, session,
                   jsonify, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import os
import shutil
from flask_socketio import SocketIO, emit, join_room, leave_room

#/from dotenv import load_dotenv
import logging

import random
import string
import re
import atexit
from user_agents import parse


def on_shutdown():
    print("Flaskアプリが終了されました")
    #print("chat.dbなどを削除します")
    #clean_files()


def clean_files():
    database_file = "chat.db"
    uploads_folder = "uploads"
    icons_folder = "icons"

    if os.path.exists(database_file):
        os.remove(database_file)
    else:
        print(f"{database_file} does not exist")

    if os.path.exists(icons_folder):
        shutil.rmtree(icons_folder)
        os.mkdir(icons_folder)
    else:
        print(f"{icons_folder} does not exist")

    if os.path.exists(uploads_folder):
        shutil.rmtree(uploads_folder)
        os.mkdir(uploads_folder)
    else:
        print(f"{uploads_folder} does not exist")


atexit.register(on_shutdown)

logging.getLogger('werkzeug').setLevel(logging.DEBUG)

app = Flask(__name__)

if False:
    app.secret_key = os.getenv('APP_SECRET_KEY')
    file_secret_key = os.getenv('FILE_SECRET_KEY')
    APP_PASSWORD = os.getenv('APP_PASSWORD')
else:
    app.secret_key = 'your_secret_key'
    file_secret_key = 'gakdscy678uiojk3led'
    APP_PASSWORD = 'goukikadan'
    print("test mode")

is_test = True

socketio = SocketIO(app, manage_session=False)  # manage_session=False は Flask sessionを直接使う時
print("socketio")

# === 設定 ===
UPLOAD_FOLDER = 'uploads'
ICON_FOLDER = 'icons'
TOKEN_EXPIRATION_MINUTES = 10

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ICON_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['ICON_FOLDER'] = ICON_FOLDER

JST = timezone(timedelta(hours=9))  # UTC+9（日本時間）


# === データベース初期化 ===
def init_db():
    with sqlite3.connect("chat.db") as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                display_name TEXT,
                password TEXT,
                icon_is_default INTEGER DEFAULT 1,
                icon_filename TEXT DEFAULT NULL,
                last_comment_time INTEGER DEFAULT -1,
                status_message TEXT DEFAULT NULL,
                friend_password TEXT DEFAULT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                icon_url TEXT,
                creator_id INTEGER,
                created_at TEXT,
                messages_table TEXT,
                others TEXT DEFAULT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id INTEGER,
                group_id TEXT,
                join_date TEXT,
                join_order INTEGER DEFAULT 0,
                last_comment_time INTEGER DEFAULT -1,
                PRIMARY KEY (user_id, group_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (group_id) REFERENCES groups(id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS read_status (
                group_id TEXT,
                user_id INTEGER,
                last_read_message_id INTEGER,
                PRIMARY KEY (group_id, user_id)
            )
        ''')
        """
        conn.execute('''
            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT,
                user_email TEXT,
                PRIMARY KEY (group_id, user_email)
            )
        ''')
        """


init_db()


# === アプリパスワード認証 ===
@app.route('/app_auth', methods=['GET', 'POST'])
def app_auth():
    # 既に認証済みの場合はログイン画面へリダイレクト
    if session.get('app_authenticated', False):
        print("既にアプリパスワード認証済みです。ログイン画面へリダイレクト")
        return redirect(url_for('login'))

    if request.method == 'POST':
        app_password = str(request.form.get('app_password'))
        if app_password == APP_PASSWORD:
            print("アプリパスワード認証成功")
            session['app_authenticated'] = True
            session.permanent = True  # セッションを永続化
            return redirect(url_for('login'))
        else:
            print("アプリパスワード認証失敗")
            return render_template('app_password.html',
                                   error="アプリパスワードが間違っています。")
    #device_type = request.args.get('device_type')
    device_type = session.get('device_type', 'pc')
    return render_template('app_password.html', device_type=device_type)


# アプリ認証チェック関数
def check_app_auth():
    return session.get('app_authenticated', False)


# === ユーザー登録 ===
@app.route('/register', methods=['GET', 'POST'])
def register():
    # アプリパスワード認証チェック
    if not check_app_auth():
        return redirect(url_for('app_auth'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        display_name = request.form.get('display_name')
        icon = request.files.get('icon')
        print(
            f"email: {email}, password: {password}, confirm_password: {confirm_password}, display_name: {display_name}, icon: {icon.filename if icon else 'None'}"
        )

        if not email or not password or not display_name:
            return render_template('register.html', error="すべての項目を入力してください。")
        if password != confirm_password:
            return render_template('register.html', error="パスワードが一致しません。")

        filename = ""
        if icon:
            ext = os.path.splitext(str(icon.filename))[1]
            filename = f"{uuid.uuid4().hex}{ext}"
            icon.save(os.path.join(app.config['ICON_FOLDER'], filename))

        hashed_password = generate_password_hash(password)
        try:
            with sqlite3.connect("chat.db") as conn:
                new_password = ""
                while True:
                    new_password = random_id(8)
                    cur = conn.execute("SELECT 1 FROM users WHERE friend_password = ?",
                        (new_password,))
                    if not cur.fetchone():
                        break  # 衝突なし
                hashed_friend_password = generate_password_hash(new_password)
                conn.execute(
                    '''
                    INSERT INTO users (email, display_name, password, icon_filename, icon_is_default, friend_password)
                    VALUES (?, ?, ?, ?, ? , ?)
                ''', (email, display_name, hashed_password, filename, 0 if icon else 1, hashed_friend_password))
        except sqlite3.IntegrityError:
            return render_template('register.html',
                                   error="このメールアドレスは既に登録されています。")

        session['user'] = email
        #return redirect(url_for('login'))
        return render_template('showPassword.html', new_password=new_password)
    return render_template('register.html')


# === ログイン ===
@app.route('/', methods=['GET', 'POST'])
def login():
    # アプリパスワード認証チェック
    if not check_app_auth():
        device_type = get_device_type()
        session['device_type'] = device_type
        return redirect(url_for('app_auth'))

    if request.method == 'POST':
        email = str(request.form.get('email'))
        password = str(request.form.get('password'))

        with sqlite3.connect("chat.db") as conn:
            cur = conn.execute(
                "SELECT id, password, display_name FROM users WHERE email=?",
                (email, ))
            row = cur.fetchone()

        if row and check_password_hash(row[1], password):
            session['user'] = email
            session['uid'] = row[0]
            session['name'] = row[2]
            #return render_template("chat_socket.html", user=row[2], email=email)
            return redirect(url_for('go_chat'))
            #return redirect(url_for('map'))

        return render_template('login.html',
                               error="メールアドレスまたはパスワードが間違っています。",
                               device_type="pc")

    else:
        #session.clear()
        session.pop('user', None)
        session.pop('uid', None)
        session.pop('name', None)
        #session.pop('app_authenticated', None)
        device_type = get_device_type()
        session['device_type'] = device_type
        return render_template('login.html', device_type=device_type)
    
@app.route('/bot')
def bot():
    return render_template("bot.html")

@app.route('/go_chat')
def go_chat():
    if 'user' not in session or session.get('user') is None:
        return redirect(url_for('login'))
        
    # アプリパスワード認証チェック
    if not check_app_auth():
        return redirect(url_for('app_auth'))
        
    return render_template("chat_socket.html", user=session.get('name'), email=session.get('user'))

@app.route('/map')
def map():
    return render_template("map.html")

def get_device_type():
    user_agent_str = request.headers.get('User-Agent')
    user_agent = parse(user_agent_str)
    device_type = ""
    if user_agent.is_mobile:
        device_type = "mobile"
        #return "モバイル端末からのアクセスです"
    elif user_agent.is_tablet:
        device_type = "tablet"
        #return "タブレットからのアクセスです"
    elif user_agent.is_pc:
        device_type = "pc"
        #return "PCからのアクセスです"
    else:
        device_type = "other"
        #return "その他の端末です"
    return device_type

# WebSocket接続時の認証チェック例
@socketio.on('connect')
def on_connect():
    # セッションのユーザー認証を確認
    user_email = session.get('user')
    user_id = session.get('uid')
    if not user_email:
        print("未認証ユーザーがWebSocket接続を試みました")
        return False  # 接続拒否

    # 接続成功時の処理
    #ユーザーが入っているグループを全て取得
    with sqlite3.connect("chat.db") as conn:
        cur3 = conn.execute(
            "SELECT group_id FROM user_groups WHERE user_id = ?", (user_id, ))
        group_ids = [r[0] for r in cur3.fetchall()]
        for group_id in group_ids:
            join_room(group_id)
    print(f"WebSocket接続成功: {user_email}")
    emit('connected', {'msg': f'ようこそ、{user_email}さん！'})

# チャット画面 first
@app.route('/get_groups')
def get_groups():
    # アプリパスワード認証チェック
    if not check_app_auth():
        return redirect(url_for('app_auth'))

    if 'user' not in session:
        return redirect(url_for('login'))

    email = session['user']

    with sqlite3.connect("chat.db") as conn:
        # ユーザー情報（表示名＋アイコン）を取得
        cur = conn.execute(
            "SELECT display_name, icon_filename, icon_is_default FROM users WHERE email=?",
            (email,)
        )
        row = cur.fetchone()
        display_name = row[0] if row else "Unknown"
        user_icon = row[1] if row and row[1] else None
        user_icon_is_default = row[2] if row else 1

        # ユーザーが所属するグループ情報を取得
        cur2 = conn.execute("""
            SELECT
                g.id,
                g.name,
                g.icon_url,
                g.creator_id,
                g.created_at,
                g.messages_table,
                g.others,
                g.type,
                u.display_name AS creator_display_name,
                u.icon_filename AS creator_icon_filename,
                u.icon_is_default AS creator_icon_is_default
            FROM groups g
            JOIN user_groups ug ON g.id = ug.group_id
            JOIN users u ON g.creator_id = u.id
            WHERE ug.user_id = ?
        """, (session['uid'],))
        result = cur2.fetchall()

        groups = []
        groups_count = 0
        DMs_count = 0

        for r in result:
            group_type = r[7]
            if group_type == "dm":
                DMs_count += 1
            else:
                groups_count += 1

            # creator_icon_url の計算
            if r[10] == 1:  # creator_icon_is_default
                creator_icon_url = "/static/default.jpeg"
            else:
                creator_icon_url = f"/icons/{r[9]}"  # creator_icon_filename

            group = {
                "id": r[0],
                "name": r[1],
                "icon_url": r[2],
                "creator_id": r[3],
                "created_at": r[4],
                "messages_table": r[5],
                "creator_display_name": r[8],
                "creator_icon_url": creator_icon_url,
                "others": r[6] if r[6] else "",
                "type": group_type
            }
            groups.append(group)


    #print(f"returning groups: {groups}")
    return jsonify({
        "user": display_name,
        "user_icon": user_icon,
        "user_icon_is_default": user_icon_is_default,
        "email": email,
        "groups": groups,
        "groups_count": groups_count,
        "DMs_count": DMs_count
    })


@app.route('/create_group', methods=['POST'])
def create_group():
    if 'user' not in session:
        return "Unauthorized", 403
    #data = request.get_json()
    group_name = request.form.get('group_name')
    print(group_name)
    group_icon = request.files.get('group_icon')
    group_icon_url = ""
    if group_icon:
        ext = os.path.splitext(str(group_icon.filename))[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        group_icon.save(os.path.join(app.config['ICON_FOLDER'], filename))
        group_icon_url = f"/icons/{filename}"
    else:
        group_icon_url = "/static/chat_default.png"
    #creater_email = session['user']
    creater_id = session['uid']
    created_at = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    #group_id = str(uuid.uuid4()) //uuidで作成→長すぎるため、入力には向いていない
    with sqlite3.connect("chat.db") as conn:
        while True:
            group_id = random_id(8)
            cur = conn.execute("SELECT 1 FROM groups WHERE id = ?",
                               (group_id, ))
            if not cur.fetchone():
                break  # 衝突なし
    group_id = group_id.replace("-", "_")
    messages_table = f"messages_{group_id}"

    with sqlite3.connect("chat.db") as conn:
        conn.execute(
            "INSERT INTO groups (id, name, type, icon_url, creator_id, created_at, messages_table) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (group_id, group_name, "group", group_icon_url, creater_id, created_at,
             messages_table))
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {messages_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                type TEXT,
                text TEXT,
                time TEXT,
                read INTEGER DEFAULT 0,
                email TEXT,
                user_id INTEGER,
                reply_to INTEGER DEFAULT -1
            )
        """)

    #return render_template('chat.html', group_id=group_id)
    return jsonify({"status": "ok", "group_id": group_id})


@socketio.on('join_group')
def join_group(data):
    if 'user' not in session:
        return "Unauthorized", 403

    group_id = data.get('group_id')
    user_id = session['uid']

    with sqlite3.connect("chat.db") as conn:
        # 既に参加しているかチェック
        cur = conn.execute(
            "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ? LIMIT 1",
            (user_id, group_id))
        if cur.fetchone():
            emit('error', {'msg': 'すでに参加しています'})
            return

        # グループ存在確認
        cur = conn.execute("SELECT name FROM groups WHERE id = ?", (group_id,))
        row = cur.fetchone()
        if row is None:
            emit('error', {'msg': '当てはまるgroupがありません'})
            return
        group_name = row[0]

        # join_order の計算
        cur = conn.execute(
            "SELECT MAX(join_order) FROM user_groups WHERE group_id = ?",
            (group_id,))
        max_order = cur.fetchone()[0] or 0

        join_date = datetime.now(JST).strftime('%Y/%m/%d')

        # read_status を先に登録（既存なら更新）
        conn.execute("""
            INSERT INTO read_status (group_id, user_id, last_read_message_id)
            VALUES (?, ?, ?)
            ON CONFLICT(group_id, user_id) DO UPDATE SET last_read_message_id = ?
        """, (group_id, user_id, 0, 0))

        # user_groups に登録（ここで初めてINSERT）
        conn.execute("""
            INSERT INTO user_groups (user_id, group_id, join_date, join_order)
            VALUES (?, ?, ?, ?)
        """, (user_id, group_id, join_date, max_order + 1))

    # Socket.IOで部屋に参加
    join_room(group_id)

    # 参加完了を通知
    emit('joined', {
        'msg': f'{group_name}に参加しました',
        'group_id': group_id,
        'group_name': group_name
    }, to=group_id)


@socketio.on('leave_group')
def leave_group(data):
    user_id = session['uid']
    group_id = data.get('group_id')
    with sqlite3.connect("chat.db") as conn:
        # user_groupsから退出するユーザーの情報を削除
        conn.execute('''
            DELETE FROM user_groups
            WHERE user_id = ? AND group_id = ?
        ''', (user_id, group_id))

        # グループが空になった場合（参加者がいなくなった場合）にグループも削除
        cursor = conn.execute('''
            SELECT COUNT(*) FROM user_groups WHERE group_id = ?
        ''', (group_id,))

        if cursor.fetchone()[0] == 0:
            conn.execute('''
                DELETE FROM groups WHERE id = ?
            ''', (group_id,))
            conn.execute(
                f"DROP TABLE IF EXISTS messages_{group_id}"
            )

        print("Successfully left the group")
        leave_room(group_id)
        emit('left_ok', {'msg': 'グループから退出しました'})
        emit('left', {'msg': 'グループから退出しました'}, to=group_id)


@app.route('/update_group_profile', methods=['POST'])
def update_group_profile():
    if 'user' not in session:
        return "Unauthorized", 403

    new_group_name = request.form.get('group_name')
    new_group_icon = request.files.get('group_icon')
    group_id = request.form.get('group_id')

    print(
        f"New group name: {new_group_name}, New group icon: {new_group_icon.filename if new_group_icon else 'None'}"
    )

    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute("SELECT icon_url FROM groups WHERE id=?",
                           (group_id, ))
        row = cur.fetchone()
        old_icon_url = row[0] if row else None
        icon_url = "/static/chat_default.png"
        # 新しいアイコンを保存してパスを作成
        if new_group_icon:
            # 保存先ディレクトリ（適宜調整）
            #upload_folder = os.path.join(app.config["ICON_FOLDER"], new_group_icon)
            #os.makedirs(upload_folder, exist_ok=True)

            # ファイル名を安全にする（例：groupid_オリジナルファイル名）
            #filename = f"group_{group_id}_{new_group_icon.filename}"
            ext = os.path.splitext(str(new_group_icon.filename))[1]
            filename = str(uuid.uuid4().hex) + ext
            filepath = os.path.join(app.config["ICON_FOLDER"], filename)

            # ファイル保存
            new_group_icon.save(filepath)

            # 古いアイコンファイルがあれば削除（ファイル存在確認してから）
            if old_icon_url != "/static/chat_default.png":
                try:
                    if old_icon_url:
                        #old_icon_path = os.path.join(app.config["ICON_FOLDER"], old_icon_url.lstrip('/'))
                        #old_icon_path = os.path.join(app.config["ICON_FOLDER"], old_icon_url.split('/')[-1])
                        if os.path.exists(old_icon_url):
                            os.remove(old_icon_url)
                except Exception as e:
                    print(f"Failed to delete old icon: {e}")

            # DBに新しいアイコンのパスをセット
            icon_url = filepath
        else:
            # アイコンは変更しない
            icon_url = old_icon_url

        # グループ名とアイコンのURLを更新
        conn.execute("UPDATE groups SET name=?, icon_url=? WHERE id=?",
                     (new_group_name, icon_url, group_id))
        conn.commit()

    # WebSocketでそのグループの全員に通知
    socketio.emit('group_profile_updated', {
        'group_id': group_id,
        'new_name': new_group_name 
    }, to=group_id)

    return jsonify({"status": "ok", "message": "Group profile updated"})


@app.route('/chat/<group_id>')
def chat(group_id):
    # ログインしているかチェック
    if 'user' not in session:
        return redirect(url_for('login'))
    # アプリパスワード認証チェック
    if 'app_authenticated' not in session:
        return redirect(url_for('app_auth'))
    # グループに参加しているかチェック
    user_id = session['uid']
    #email = session['user']
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute(
            "SELECT 1 FROM user_groups WHERE user_id = ? AND group_id = ? LIMIT 1",
            (user_id, group_id))
        result = cur.fetchone()
        if not result:
            return jsonify({'status': 'error', 'message': 'グループに参加していません'})
        # メッセージ取得（名前だけでなく送信者のアイコンも含める場合はクエリ変更が必要）
        messages_table = f"messages_{group_id}"
        cur9 = conn.execute(f"SELECT MAX(id) FROM {messages_table}")
        row = cur9.fetchone()  # 結果はタプルで返るので [0] をつける
        max_id = row[0] if row[0] is not None else 0

        latest_read_message_id = get_latest_read(group_id)
        if latest_read_message_id is False:
            return jsonify({'status': 'error', 'message': '読み込みに失敗しました'})
        if max_id > latest_read_message_id:
            conn.execute(
                "UPDATE read_status SET last_read_message_id = ? WHERE group_id = ? AND user_id = ?",
                (max_id, group_id, user_id)
            )
            conn.execute(
                f"UPDATE {messages_table} SET read = read + 1 WHERE id > ?",
                (latest_read_message_id,)
            )

        cur_participants = conn.execute('''
            SELECT u.email
            FROM users u
            JOIN user_groups ug ON u.id = ug.user_id
            WHERE ug.group_id = ?
        ''', (group_id,))

        # 参加しているユーザーのemail一覧を取得
        participants_emails = [email for email, in cur_participants.fetchall()]
        
        cur = conn.execute(
            f"SELECT id, name, type, text, time, read, email, reply_to FROM {messages_table}"
        )
        raw_messages = cur.fetchall()
        messages = []
        emails = [msg[6] for msg in raw_messages]  # メッセージに含まれるemailを抽出
        cur_users = conn.execute('''
            SELECT email, icon_filename, icon_is_default, id 
            FROM users WHERE email IN ({})
        '''.format(','.join('?' for _ in emails)), emails)

        # ユーザー情報を辞書にまとめる
        users_info = {email: (icon_filename, icon_is_default, user_id) for email, icon_filename, icon_is_default, user_id in cur_users.fetchall()}
        for id, name, type, text, time_, read, email_, reply_to in raw_messages:
            icon_filename, icon_is_default, user_id = users_info.get(email_, (None, 1, None))

            # グループに参加していない場合は名前に "(退出済み)" を付け加える
            if email_ not in participants_emails:
                name += " (退出済み)"
            date = datetime.strptime(time_, '%Y-%m-%d %H:%M:%S').strftime('%Y/%m/%d')
            time = datetime.strptime(time_, '%Y-%m-%d %H:%M:%S').strftime('%H:%M')
            messages.append({
                "id": id,
                "name": name,
                "user_id": user_id,
                "type": type,
                "text": text,
                "time": time,
                "date": date,
                "read": read - 1,
                "email": email_,
                "reply_to": reply_to,
                "icon": icon_filename,
                "icon_is_default": icon_is_default
            })
    #print(f"returning messages: {messages}")
    return jsonify({"status": "ok", "messages": messages, "file_secret_key": file_secret_key})

@socketio.on('read_message')
def read_message(data):
    if 'user' not in session:
        return
    group_id = data.get('group_id')
    message_id = data.get('message_id')
    user_id = session['uid']

    with sqlite3.connect("chat.db") as conn:
        conn.execute(
            "UPDATE read_status SET last_read_message_id = ? WHERE group_id = ? AND user_id = ?",
            (message_id, group_id, user_id)
        )
        conn.execute(
            f"UPDATE messages_{group_id} SET read = read + 1 WHERE id = ?",
            (message_id,)
        )
        conn.commit()
    emit('update_read_message', {'group_id': group_id, 'message_id': message_id}, to=group_id)

#@app.route('/get_latest_read/<group_id>')
def get_latest_read(group_id):
    if 'user' not in session:
        #return "Unauthorized", 403
        return False
    user_id = session['uid']
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute(
            "SELECT last_read_message_id FROM read_status WHERE group_id = ? AND user_id = ?",
            (group_id, user_id)
        )
        row = cur.fetchone()
        if row is None:
            #return jsonify({"status": "error", "message": "読み込みに失敗しました"}), 400
            return False
        last_read_message_id = row[0]
    return last_read_message_id
    #return jsonify({"status": "ok", "last_read_message_id": last_read_message_id})


# === アイコンアップロード（ログイン中） ===
@app.route('/upload_icon', methods=['POST'])
def upload_icon():
    if 'user' not in session:
        return "Unauthorized", 403

    file = request.files.get('icon')
    if not file:
        return "No file", 400

    ext = os.path.splitext(str(file.filename))[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(app.config['ICON_FOLDER'], filename))

    email = session['user']
    with sqlite3.connect("chat.db") as conn:
        conn.execute(
            "UPDATE users SET icon_filename=?, icon_is_default=0 WHERE email=?",
            (filename, email))

    return jsonify({"status": "ok", "filename": filename})


#ファイルのアップロード
@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        return "Unauthorized", 403

    file = request.files.get('file')
    if not file:
        return "No file", 400
    group_id = request.form.get('group_id')
    replyToId = request.form.get('replyTo')
    file_type = request.form.get('fileType')
    table_name = f"messages_{group_id}"

    filename = file.filename
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], str(filename))
    file.save(save_path)
    #id = str(uuid.uuid4())
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now_sec = int(datetime.now(timezone.utc).timestamp())
    email = session['user']
    user_id = session['uid']
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute("SELECT display_name FROM users WHERE email=?",
                           (email, ))
        row = cur.fetchone()
        name = row[0] if row else "Unknown"
        conn.execute(
            f"INSERT INTO {table_name} (name, type, text, time, read, email, user_id, reply_to) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, file_type, filename, now, 0, email, user_id,
             replyToId if replyToId else -1))
        conn.execute(
            '''
            UPDATE user_groups SET last_comment_time = ?
            WHERE user_id = ? AND group_id = ?
        ''', (now_sec, user_id, group_id))
    socketio.emit('new_message', {
        'group_id': group_id,
        'sender_id': user_id
    }, to=group_id)
    return "Uploaded", 200


@app.route('/get_group_info', methods=['POST'])
def get_group_info():
    data = request.get_json()
    group_id = data.get('group_id')
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute("SELECT name, icon_url, type FROM groups WHERE id = ?",
                           (group_id, ))
        row = cur.fetchone()
        if row is None:
            return jsonify({"status": "error", "message": "グループが存在しません"}), 400
        group_name = row[0]
        group_icon_url = row[1]
    return jsonify({
        "status": "ok",
        "group_name": group_name,
        "group_icon_url": group_icon_url
    })


@app.route('/files/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


#アイコン取得
@app.route('/icons/<filename>')
def serve_icon(filename):
    return send_from_directory(app.config['ICON_FOLDER'], str(filename))


@app.route('/api/update-profile', methods=['POST'])
def update_profile():
    if 'user' not in session:
        print("Unauthorized access to update profile")
        return "Unauthorized", 403

    print("Updating profile...")

    email = session['user']
    user_id = session.get('uid')
    new_name = request.form.get('name')
    icon = request.files.get('icon')
    status_message = request.form.get('status_message')
    print(
        f"New name: {new_name}, New icon: {icon.filename if icon else 'None'}")
    print("updating database...")

    with sqlite3.connect("chat.db") as conn:
        # 旧ユーザー情報を取得
        cur = conn.execute(
            "SELECT icon_filename, icon_is_default FROM users WHERE email=?",
            (email, ))
        row = cur.fetchone()
        old_icon_filename = row[0]
        old_icon_is_default = row[1]

        update_fields = []
        update_values = []

        if new_name:
            update_fields.append("display_name=?")
            update_values.append(new_name)
        update_fields.append("status_message=?")
        update_values.append(status_message)

        if icon and icon.filename:
            ext = os.path.splitext(secure_filename(icon.filename))[1]
            filename = f"{uuid.uuid4().hex}{ext}"
            save_path = os.path.join(app.config['ICON_FOLDER'], filename)
            os.makedirs(app.config['ICON_FOLDER'], exist_ok=True)
            icon.save(save_path)

            if not old_icon_is_default:
                try:
                    os.remove(
                        os.path.join(app.config['ICON_FOLDER'],
                                     old_icon_filename))
                except FileNotFoundError:
                    pass
                except Exception as e:
                    print(f"Failed to delete old icon: {e}")

            update_fields.extend(["icon_filename=?", "icon_is_default=?"])
            update_values.extend([filename, 0])

        update_values.append(email)
        if update_fields:
            conn.execute(
                f"UPDATE users SET {', '.join(update_fields)} WHERE email=?",
                update_values)

        # ===== ここから：各グループのmessagesテーブルのnameも更新 =====
        if new_name and user_id:
            cur2 = conn.execute(
                """
                SELECT g.messages_table
                FROM groups g
                JOIN user_groups ug ON g.id = ug.group_id
                WHERE ug.user_id = ?
            """, (user_id, ))
            group_tables = [r[0] for r in cur2.fetchall()]

            for table_name in group_tables:
                try:
                    conn.execute(
                        f"UPDATE {table_name} SET name=? WHERE email=?",
                        (new_name, email))
                except sqlite3.OperationalError as e:
                    print(f"テーブル {table_name} の更新中にエラー: {e}")

    #ユーザーが入っているグループを全て取得
    cur3 = conn.execute(
        "SELECT group_id FROM user_groups WHERE user_id = ?", (user_id, ))
    group_ids = [r[0] for r in cur3.fetchall()]
    socketio.emit('my_profile_updated', {
        'user_id': user_id,
        'new_name': new_name
    })
    for group_id in group_ids:
        socketio.emit('profile_updated', {
            'group_id': group_id, 
            'user_id': user_id
        }, to=group_id)

    print("update profile successfully")
    return jsonify({"success": True})


# === メッセージ送信（グループ対応）===
@socketio.on('send')
def send_message(data):
    print("\n\nSending message...")

    if 'user' not in session:
        return "Unauthorized", 403

    #data = request.get_json()
    text = data.get('text')
    group_id = data.get('group_id')  # ★ クライアントから group_id を受け取る
    replyToId = data.get('replyTo')

    if not group_id or not text:
        return jsonify({"status": "error", "message": "Invalid input"})

    email = session['user']
    user_id = session['uid']

    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute("SELECT display_name FROM users WHERE email=?",
                           (email, ))
        row = cur.fetchone()

        if row is None:
            return "User not found", 404

        name = row[0]

        now = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
        now_sec = int(datetime.now(JST).timestamp())

        # === 動的テーブル名に注意 ===
        table_name = f"messages_{group_id}"

        # SQLインジェクション対策のため、テーブル名のバリデーション（数字のみ）
        #if not table_name.replace("messages_", "").isdigit():
        #return "Invalid group_id", 400

        conn.execute(
            f'''
            INSERT INTO {table_name} (name, type, text, time, read, email, user_id, reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, "text", text, now, 0, email, user_id,
              replyToId if replyToId else -1))

        conn.execute(
            '''
            UPDATE user_groups SET last_comment_time = ?
            WHERE user_id = ? AND group_id = ?
        ''', (now_sec, user_id, group_id))

    print(f"[送信メッセージ] ({group_id}) {text}")
    #return jsonify({"status": "ok", "message": text, "time": now})
    emit('new_message', {
        'group_id': group_id,
        'sender_id': user_id
    }, to=group_id)


def human_readable_time(timestamp):
    if timestamp == -1:
        return "コメントなし"
    now = int(datetime.now(JST).timestamp())
    diff = now - timestamp
    if diff < 60:
        return "今"
    elif diff < 3600:
        return f"{diff // 60}分前"
    elif diff < 86400:
        return f"{diff // 3600}時間前"
    elif diff < 2592000:
        return f"{diff // 86400}日前"
    elif diff < 31536000:
        return f"{diff // 2592000}ヶ月前"
    else:
        return "ずっと前"

# ログアウト
@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user", None)
    session.pop("uid", None)
    session.pop("app_authenticated", None)
    session.clear()
    resp = jsonify({"status": "ok"})
    resp.set_cookie("session", "", expires=0)  # 👈 Cookieを強制削除
    return resp


@app.route("/login")
def login_redirect():
    return redirect(url_for("login"))


@app.route("/api/user-info")
def user_info():
    #print("Fetching user info...")
    uid = session.get("uid")
    if not uid:
        return jsonify({"error": "未ログイン"}), 401

    with sqlite3.connect("chat.db") as db:
        db.row_factory = sqlite3.Row
        user = db.execute(
            "SELECT id, display_name, icon_filename, icon_is_default, email, status_message FROM users WHERE id = ?",
            (uid, )).fetchone()
        if not user:
            print("User not found")
            return jsonify({"error": "ユーザーが見つかりません"}), 404
        #print(f"User info fetched: {user['display_name']}, icon: {user['icon_filename']}, email: {user['email']}, is_default: {user['icon_is_default']}")
        return jsonify({
            "id": user["id"],
            "email": user["email"],
            "name": user["display_name"],
            "iconUrl": ("/static/default.jpeg" if user["icon_is_default"] else f"/icons/{user['icon_filename']}"),
            "icon_is_default": user["icon_is_default"],
            "status_message": user["status_message"]
        })


@app.route("/send_debug", methods=["POST"])
def receive_message():
    data = request.get_json()
    text = data.get("text")

    print(f"[受信メッセージ] {text}")  # ← ターミナルに出る
    return jsonify({"status": "ok"})


@app.route("/api/members/<group_id>")
def get_members(group_id):

    with sqlite3.connect("chat.db") as conn:
        #cur = conn.execute("SELECT id, display_name, icon_filename, icon_is_default, last_comment_time FROM users")
        #members = [{"id": r[0], "name": r[1], "icon": r[2], "icon_is_default": r[3], "last_comment_time": r[4]} for r in cur.fetchall()]
        cur = conn.execute(
            """
            SELECT u.id, u.display_name, u.icon_filename, u.icon_is_default, ug.last_comment_time, ug.join_date, ug.join_order
            FROM users u
            JOIN user_groups ug ON u.id = ug.user_id
            WHERE ug.group_id = ?
        """, (group_id, ))


        members = [{
            "id": r[0],
            "name": r[1],
            "icon": r[2],
            "icon_is_default": r[3],
            "last_comment_time": r[4],
            "join_date": r[5],
            "join_order": r[6]
        } for r in cur.fetchall()]

        cur1 = conn.execute("SELECT type FROM groups WHERE id = ?", (group_id,))
        row = cur1.fetchone()
        if row is not None and row[0] == "dm":
            type = "dm"
        else:
            type = "group"
        
        for member in members:
            member["last_comment_time_readable"] = human_readable_time(
                member["last_comment_time"])
        #conn.commit()
        return jsonify(members=members, type=type)


@app.route('/users/<user_id>/<group_id>')
def get_user_info(user_id, group_id):
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute(
            "SELECT display_name, icon_filename, icon_is_default, status_message FROM users WHERE id = ?",
            (user_id, ))
        cur_new = conn.execute(
            "SELECT join_date, join_order FROM user_groups WHERE user_id = ? AND group_id = ?",
            (user_id, group_id))
        row = cur.fetchone()
        row_new = cur_new.fetchone()
        if row is None:
            return jsonify({"status": "error", "message": "ユーザーが存在しません"}), 400
        display_name = row[0]
        icon_filename = row[1]
        icon_is_default = row[2]
        status_message = row[3]
        join_date = row_new[0] if row_new else "-"
        join_order = row_new[1] if row_new else "-"
        return jsonify({
            "status": "ok",
            "display_name": display_name,
            "icon_filename": icon_filename,
            "icon_is_default": icon_is_default,
            "status_message": status_message,
            "join_date": join_date,
            "join_order": join_order
        })


def random_id(length=8):
    chars = string.ascii_letters + string.digits  # 英大文字＋小文字＋数字
    return ''.join(random.choice(chars) for _ in range(length))


@app.route('/clean_broken_groups', methods=["POST"])
def clean_broken_groups(db_path="chat.db"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 現存する全テーブル名を取得
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = set(row[0] for row in c.fetchall())

    # groups から group_id を取得
    c.execute("SELECT id FROM groups")
    group_ids = [row[0] for row in c.fetchall()]
    group_id_set = set(group_ids)

    deleted_groups = []
    deleted_orphan_tables = []

    # --- groups にあるけど不完全なグループを削除 ---
    for group_id in group_ids:
        safe_group_id = re.sub(r'\W+', '_', group_id)
        messages_table = f"messages_{safe_group_id}"

        has_messages_table = messages_table in all_tables

        # メンバーの存在確認
        c.execute("SELECT COUNT(*) FROM group_members WHERE group_id = ?",
                  (group_id, ))
        has_members = c.fetchone()[0] > 0

        if not has_messages_table or not has_members:
            print(
                f"[削除] グループ '{group_id}'：テーブル存在={has_messages_table}、メンバー={has_members}"
            )

            # groups テーブルと group_members から削除
            c.execute("DELETE FROM groups WHERE group_id = ?", (group_id, ))
            c.execute("DELETE FROM group_members WHERE group_id = ?",
                      (group_id, ))

            # messages テーブルがあれば削除
            if has_messages_table:
                c.execute(f'DROP TABLE IF EXISTS "{messages_table}"')

            deleted_groups.append(group_id)

    # --- messages_ テーブルにあるけど、groups にないものを削除 ---
    for table in all_tables:
        if table.startswith("messages_"):
            # group_id を逆算（messages_group1 → group1）
            suffix = table[len("messages_"):]
            guessed_group_id = suffix.replace("_", "-")  # 自分で変換したルールに合わせて調整する

            if guessed_group_id not in group_id_set:
                print(f"[削除] 孤立テーブル '{table}'（対応する group_id が存在しない）")
                c.execute(f'DROP TABLE IF EXISTS "{table}"')
                deleted_orphan_tables.append(table)

    conn.commit()
    conn.close()

    print("\n=== グループ・テーブルクリーンアップ完了 ===")
    if deleted_groups:
        print(f"削除された groups エントリ: {deleted_groups}")
    if deleted_orphan_tables:
        print(f"削除された孤立 messages テーブル: {deleted_orphan_tables}")
    if not deleted_groups and not deleted_orphan_tables:
        print("削除された項目はありません。データは健全です。")

    return jsonify({
        "status": "ok",
        "deleted_groups": deleted_groups,
        "deleted_orphan_tables": deleted_orphan_tables
    })

@app.route('/onClose', methods=["POST"])
def onClose():
    print("onClose")
    session.pop("user", None)
    session.pop("uid", None)
    session.pop("name", None)
    
    #session.pop("app_authenticated", None)
    #session.clear()
    resp = jsonify({"status": "ok"})
    #resp.set_cookie("session", "", expires=0)  # 👈 Cookieを強制削除
    return resp

@socketio.on('regenerate_friend_password')
def regenerate_friend_password():
    print("Regenerating friend password...")
    with sqlite3.connect("chat.db") as conn:
        uid = session.get('uid')
        if not uid:
            print("User ID not found in session.")
            emit('error', {'status': 'error', 'msg': 'ログインが必要です'})
            return
        new_password = ""
        while True:
            new_password = random_id(8)
            cur = conn.execute("SELECT 1 FROM users WHERE friend_password = ?",
                (new_password,))
            if not cur.fetchone():
                break  # 衝突なし
        hashed_friend_password = generate_password_hash(new_password)
        conn.execute("""
            UPDATE users SET friend_password = ? WHERE id = ?
        """, (hashed_friend_password, uid))
        emit('friend_password_regenerated', {'status': 'ok', 'new_password': new_password})

def update_():
    with sqlite3.connect("chat.db") as conn:
        cur = conn.execute("""
            SELECT id FROM users
        """)
        user_ids = [row[0] for row in cur.fetchall()]
        print(f"User IDs: {user_ids}")
        
        for user_id in user_ids:
            new_password = ""
            while True:
                new_password = random_id(8)
                cur = conn.execute("SELECT 1 FROM users WHERE friend_password = ?",
                    (new_password,))
                if not cur.fetchone():
                    break  # 衝突なし
            hashed_friend_password = generate_password_hash(new_password)
            conn.execute("""
                UPDATE users SET friend_password = ? WHERE id = ?
            """, (hashed_friend_password, user_id))

@socketio.on('add_friend')
def add_friend(data):
    friend_id = data.get('friend_id')
    friend_password = data.get('friend_password')
    my_id = session.get('uid')  # セッションから自分のIDを取得（POSTならrequest.formやJSONから）
    print(f"friend_id: {friend_id}, friend_password: {friend_password}, my_id: {my_id}")

    if not my_id:
        emit('error', {'status': 'error', 'msg': 'ログインが必要です'})
        return

    with sqlite3.connect("chat.db") as conn:
        # 1. 相手ユーザーの存在確認 & パスワード一致
        cur = conn.execute("""
            SELECT display_name, icon_filename, icon_is_default, friend_password
            FROM users
            WHERE id = ?
        """, (friend_id, ))
        friend = cur.fetchone()
        if not friend:
            emit('error', {'status': 'error', 'msg': 'IDが間違っています'})
            return
        if not check_password_hash(friend[3], friend_password):
            emit('error', {'status': 'error', 'msg': 'パスワードが間違っています'})
            return

        # 2. 既にDMグループがあるか確認（ID順で固定）
        group_id = f"dm_{min(int(my_id), int(friend_id))}_{max(int(my_id), int(friend_id))}"
        cur = conn.execute("SELECT 1 FROM groups WHERE id = ?", (group_id,))
        if not cur.fetchone():
            # 3. グループ作成（アイコンは固定、名前は相手の名前）
            group_name = f"{friend[1]}"
            group_icon_url = f"/icons/{friend[2]}" if friend[3] else "/static/chat_default.png"
            created_at = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
            messages_table = f"messages_{group_id}"

            conn.execute("""
                INSERT INTO groups (id, name, type, icon_url, creator_id, created_at, messages_table, others)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (group_id, group_name, "dm", group_icon_url, my_id, created_at, messages_table, friend_id))

            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {messages_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    type TEXT,
                    text TEXT,
                    time TEXT,
                    read INTEGER DEFAULT 0,
                    email TEXT,
                    user_id INTEGER,
                    reply_to INTEGER DEFAULT -1
                )
            """)

            # 両者を user_groups に登録
            for uid in (my_id, friend_id):
                conn.execute("""
                    INSERT OR IGNORE INTO user_groups (user_id, group_id, join_date)
                    VALUES (?, ?, datetime('now'))
                """, (uid, group_id))

        conn.commit()

    # 4. 成功レスポンス
    emit('add_friend_response', {
        'status': 'ok',
        'friend': {
            'id': friend_id,
            'display_name': friend[0],
            'icon_filename': friend[1],
            'icon_is_default': friend[2]
        },
        'group_id': group_id
    })


if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(update_, 'cron', hour=0, minute=0)
    scheduler.start()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), use_reloader=False, log_output=True)
