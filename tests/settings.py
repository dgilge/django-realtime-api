import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


DEBUG = True


SECRET_KEY = 'dummy'


INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.staticfiles',
    'channels',
    'rest_framework',
    'realtime_api',
    'tests',
)


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        'OPTIONS': {
            'timeout': 60,
        },
        'TEST': {
            'NAME': os.path.join(BASE_DIR, 'db_test.sqlite3'),
        },
    },
}


SESSION_ENGINE = 'django.contrib.sessions.backends.cache'


MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
]


CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}


ASGI_APPLICATION = 'tests.routing.application'


ROOT_URLCONF = 'tests.urls'


STATIC_ROOT = os.path.join(BASE_DIR, 'static/apps')


STATIC_URL = '/static/'


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'templates'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
            ],
        },
    },
]


LOGOUT_REDIRECT_URL = '/auth/login/'
