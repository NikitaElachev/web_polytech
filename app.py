import os
import hashlib
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import bleach
import markdown
from models import db, User, Role, Book, Genre, Cover, Review, Collection

# Инициализация приложения и базовые настройки
app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///library.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Создание директории для хранения обложек если она отсутствует
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'covers')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Подключение базы данных и менеджера авторизации
db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Для выполнения данного действия необходимо пройти процедуру аутентификации'
login_manager.login_message_category = 'warning'

# Функция для получения текущего пользователя
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Декоратор для проверки прав доступа по ролям
def require_role(*roles):
    def wrapper(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role.name not in roles:
                flash('У вас недостаточно прав для выполнения данного действия', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return wrapper

# Функция для очистки текста от потенциально опасных HTML тегов
def sanitize_html(text):
    allowed_tags = ['p', 'b', 'i', 'u', 'em', 'strong', 'a', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'br']
    allowed_attrs = {'a': ['href', 'title']}
    return bleach.clean(text, tags=allowed_tags, attributes=allowed_attrs, strip=True)

# Главная страница со списком книг и пагинацией
@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    pagination = Book.query.order_by(Book.year.desc()).paginate(page=page, per_page=10)
    return render_template('index.html', pagination=pagination)

# Обработка авторизации пользователя
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login = request.form.get('login')
        password = request.form.get('password')
        remember = request.form.get('remember') == 'on'
        
        user = User.query.filter_by(login=login).first()
        
        # Сравнение введённого пароля с хэшем из базы
        if user and check_password_hash(user.password_hash, password):
            login_user(user, remember=remember)
            return redirect(url_for('index'))
        else:
            flash('Невозможно аутентифицироваться с указанными логином и паролем', 'danger')
            
    return render_template('login.html')

# Выход пользователя из системы
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

# Добавление новой книги администратором
@app.route('/book/add', methods=['GET', 'POST'])
@login_required
@require_role('Администратор')
def add_book():
    genres = Genre.query.all()
    if request.method == 'POST':
        # Создаем временный объект книги для сохранения введённых данных в форме при ошибке
        temp_book = Book(
            title=request.form.get('title'),
            author=request.form.get('author'),
            publisher=request.form.get('publisher'),
            year=int(request.form.get('year') or 0),
            page_count=int(request.form.get('page_count') or 0),
            short_description=request.form.get('short_description', '')
        )
        
        # Привязка выбранных жанров к временной книге
        selected_genres = request.form.getlist('genres')
        for genre_id in selected_genres:
            genre = db.session.get(Genre, int(genre_id))
            if genre:
                temp_book.genres.append(genre)

        # Серверная валидация пустого описания
        clean_description = sanitize_html(temp_book.short_description).strip()
        if not clean_description:
            flash('Поле "Краткое описание" обязательно для заполнения.', 'danger')
            return render_template('book_form.html', genres=genres, action='Добавить', book=temp_book)
        
        # Если всё заполнено, присваиваем очищенное описание
        temp_book.short_description = clean_description

        try:
            cover_file = request.files.get('cover')
            db.session.add(temp_book)
            db.session.flush()

            # Обработка загрузки файла обложки и проверка на уникальность по MD5
            if cover_file and cover_file.filename != '':
                file_content = cover_file.read()
                md5_hash = hashlib.md5(file_content).hexdigest()
                cover_file.seek(0)
                
                existing_cover = Cover.query.filter_by(md5_hash=md5_hash).first()
                if existing_cover:
                    # Использование параметров существующего файла для исключения дубликатов
                    filename = existing_cover.file_name
                    mime_type = existing_cover.mime_type
                else:
                    mime_type = cover_file.mimetype
                    temp_cover = Cover(file_name="temp", mime_type=mime_type, md5_hash=md5_hash, book_id=temp_book.id)
                    db.session.add(temp_cover)
                    db.session.flush()
                    filename = f"{temp_cover.id}_{secure_filename(cover_file.filename)}"
                    temp_cover.file_name = filename
                    
                    # Сохранение файла на диск
                    cover_file.save(os.path.join(UPLOAD_FOLDER, filename))
                
                if existing_cover:
                     new_cover = Cover(file_name=filename, mime_type=mime_type, md5_hash=md5_hash, book_id=temp_book.id)
                     db.session.add(new_cover)

            db.session.commit()
            flash('Книга успешно добавлена!', 'success')
            return redirect(url_for('view_book', book_id=temp_book.id))
        except Exception:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')
            return render_template('book_form.html', genres=genres, action='Добавить', book=temp_book)

    return render_template('book_form.html', genres=genres, action='Добавить')

# Редактирование информации о книге
@app.route('/book/<int:book_id>/edit', methods=['GET', 'POST'])
@login_required
@require_role('Администратор', 'Модератор')
def edit_book(book_id):
    book = db.get_or_404(Book, book_id)
    genres = Genre.query.all()
    
    if request.method == 'POST':
        # Обновление основных полей в объекте
        book.title = request.form['title']
        book.author = request.form['author']
        book.publisher = request.form['publisher']
        book.year = int(request.form['year'])
        book.page_count = int(request.form['page_count'])
        book.short_description = request.form['short_description']
        
        # Перезапись жанров
        book.genres = []
        selected_genres = request.form.getlist('genres')
        for genre_id in selected_genres:
            genre = db.session.get(Genre, int(genre_id))
            if genre:
                book.genres.append(genre)
                
        # Серверная валидация пустого описания
        clean_description = sanitize_html(book.short_description).strip()
        if not clean_description:
            flash('Поле "Краткое описание" обязательно для заполнения.', 'danger')
            return render_template('book_form.html', book=book, genres=genres, action='Редактировать')
        
        book.short_description = clean_description

        try:
            db.session.commit()
            flash('Книга успешно обновлена!', 'success')
            return redirect(url_for('view_book', book_id=book.id))
        except Exception:
            db.session.rollback()
            flash('При сохранении данных возникла ошибка. Проверьте корректность введённых данных.', 'danger')

    return render_template('book_form.html', book=book, genres=genres, action='Редактировать')

# Удаление книги и файла обложки
@app.route('/book/<int:book_id>/delete', methods=['POST'])
@login_required
@require_role('Администратор')
def delete_book(book_id):
    book = db.get_or_404(Book, book_id)
    try:
        if book.cover:
            file_path = os.path.join(UPLOAD_FOLDER, book.cover.file_name)
            # Считаем сколько записей в БД используют этот же файл
            usage_count = Cover.query.filter_by(file_name=book.cover.file_name).count()
            
            # Удаляем файл с диска только если это последняя книга с такой обложкой
            if usage_count <= 1 and os.path.exists(file_path):
                os.remove(file_path)
        
        db.session.delete(book)
        db.session.commit()
        flash('Книга успешно удалена!', 'success')
    except Exception:
        db.session.rollback()
        flash('Ошибка при удалении книги.', 'danger')
    return redirect(url_for('index'))

# Отображение подробной информации о книге
@app.route('/book/<int:book_id>')
def view_book(book_id):
    book = db.get_or_404(Book, book_id)
    book.html_description = markdown.markdown(book.short_description)
    
    # Проверка наличия рецензии от текущего пользователя
    user_review = None
    if current_user.is_authenticated:
        user_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    
    # Загрузка подборок для предоставления возможности добавления
    user_collections = []
    if current_user.is_authenticated and current_user.role.name == 'Пользователь':
        user_collections = Collection.query.filter_by(user_id=current_user.id).all()

    # Конвертация Markdown в HTML для отображения рецензий
    for review in book.reviews:
        review.text = markdown.markdown(review.text)

    return render_template('book_view.html', book=book, user_review=user_review, user_collections=user_collections)
    
# Форма создания рецензии на книгу
@app.route('/book/<int:book_id>/review', methods=['GET', 'POST'])
@login_required
def add_review(book_id):
    book = db.get_or_404(Book, book_id)
    existing_review = Review.query.filter_by(book_id=book_id, user_id=current_user.id).first()
    
    # Запрет на повторное создание рецензии
    if existing_review:
        flash('Вы уже оставили рецензию на эту книгу.', 'warning')
        return redirect(url_for('view_book', book_id=book_id))

    if request.method == 'POST':
        rating = int(request.form.get('rating'))
        raw_text = request.form.get('text')
        clean_text = sanitize_html(raw_text).strip()
        
        # Серверная валидация текста рецензии
        if not clean_text:
            flash('Текст рецензии не может быть пустым.', 'danger')
            return render_template('review_form.html', book=book)
            
        try:
            review = Review(book_id=book_id, user_id=current_user.id, rating=rating, text=clean_text)
            db.session.add(review)
            db.session.commit()
            flash('Рецензия успешно добавлена!', 'success')
            return redirect(url_for('view_book', book_id=book_id))
        except Exception:
            db.session.rollback()
            flash('Ошибка при сохранении рецензии.', 'danger')

    return render_template('review_form.html', book=book)

# Просмотр списка пользовательских подборок
@app.route('/collections')
@login_required
@require_role('Пользователь')
def my_collections():
    collections = Collection.query.filter_by(user_id=current_user.id).all()
    return render_template('collections.html', collections=collections)

# Создание новой пустой подборки
@app.route('/collections/add', methods=['POST'])
@login_required
@require_role('Пользователь')
def add_collection():
    name = request.form.get('name')
    if name:
        try:
            new_col = Collection(name=name, user_id=current_user.id)
            db.session.add(new_col)
            db.session.commit()
            flash('Подборка успешно создана!', 'success')
        except Exception:
            db.session.rollback()
            flash('Ошибка при создании подборки.', 'danger')
    return redirect(url_for('my_collections'))

# Отображение книг внутри выбранной подборки
@app.route('/collections/<int:collection_id>')
@login_required
@require_role('Пользователь')
def view_collection(collection_id):
    collection = db.get_or_404(Collection, collection_id)
    
    # Защита от просмотра чужих подборок
    if collection.user_id != current_user.id:
        abort(403)
    return render_template('collection_view.html', collection=collection)

# Процесс добавления книги в существующую подборку
@app.route('/collections/add_book/<int:book_id>', methods=['POST'])
@login_required
@require_role('Пользователь')
def add_book_to_collection(book_id):
    book = db.get_or_404(Book, book_id)
    collection_id = request.form.get('collection_id')
    collection = db.session.get(Collection, int(collection_id))
    
    # Проверка владельца подборки и отсутствия книги в ней
    if collection and collection.user_id == current_user.id:
        if book not in collection.books:
            try:
                collection.books.append(book)
                db.session.commit()
                flash(f'Книга добавлена в подборку "{collection.name}"!', 'success')
            except Exception:
                db.session.rollback()
                flash('Ошибка при добавлении в подборку.', 'danger')
        else:
            flash('Книга уже в этой подборке.', 'warning')
            
    return redirect(url_for('view_book', book_id=book_id))

# Наполнение базы данных тестовыми данными при пустых таблицах
def init_db():
    with app.app_context():
        db.create_all()
        # Данные будут добавлены только если таблица ролей пуста
        if not Role.query.first():
            roles = [Role(name='Администратор', description='Полные права'), 
                     Role(name='Модератор', description='Редактирование книг'), 
                     Role(name='Пользователь', description='Только рецензии и подборки')]
            db.session.add_all(roles)
            db.session.commit()
            
            # Основные пользователи
            admin = User(login='admin', password_hash=generate_password_hash('123'), last_name='Админ', first_name='Админ', role_id=1)
            moder = User(login='moder', password_hash=generate_password_hash('123'), last_name='Модер', first_name='Модер', role_id=2)
            user = User(login='user', password_hash=generate_password_hash('123'), last_name='Юзер', first_name='Юзер', role_id=3)
            user2 = User(login='user2', password_hash=generate_password_hash('123'), last_name='Елачев', first_name='Никита', role_id=3)
            user3 = User(login='user3', password_hash=generate_password_hash('123'), last_name='Иванов', first_name='Иван', role_id=3)
            
            db.session.add_all([admin, moder, user, user2, user3])
            
            # Список жанров
            genres = [
                Genre(name='Фантастика'), Genre(name='Детектив'), Genre(name='Роман'),
                Genre(name='Фэнтези'), Genre(name='Ужасы'), Genre(name='Приключения'),
                Genre(name='Комедия'), Genre(name='Триллер'), Genre(name='Научная литература'),
                Genre(name='Биография'), Genre(name='Поэзия'), Genre(name='Драма')
            ]
            db.session.add_all(genres)
            db.session.commit()

# Запуск локального сервера
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
