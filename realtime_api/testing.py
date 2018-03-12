import asyncio
import itertools
import pytest
from importlib import import_module
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest, SimpleCookie

counter = itertools.count()

User = get_user_model()


@database_sync_to_async
def create_user(username=None, password='pw', **kwargs):
    # Needed because the database isn't cleaned up after each test
    # in an async context
    if username is None:
        username = 'u{}'.format(next(counter))
    return User.objects.create_user(username, password, **kwargs)


@pytest.fixture
async def user(db):
    return await create_user()


@pytest.fixture
async def admin(db):
    return await create_user(is_staff=True, is_superuser=True)


class AuthWebsocketCommunicator(WebsocketCommunicator):

    async def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        await instance.__init__(*args, **kwargs)
        return instance

    async def __init__(
        self,
        application,
        path,
        headers=None,
        subprotocols=None,
        user=None
    ):
        if user is not None:
            await self.force_login(user)
            cookie_header = (b'cookie', self._session_cookie)
            if headers:
                index = None
                for i, header in enumerate(headers):
                    if header[0] == cookie_header[0]:
                        index = i
                        break

                if index is None:
                    headers.append(cookie_header)
                else:
                    headers[index] = (
                        cookie_header[0],
                        b'; '.join((cookie_header[1], headers[index][1]))
                    )
            else:
                headers = [cookie_header]

        super().__init__(application, path, headers, subprotocols)
        self.scope['user'] = user

    async def _login(self, user, backend=None):
        from django.contrib.auth import login

        engine = import_module(settings.SESSION_ENGINE)

        # Create a fake request to store login details.
        request = HttpRequest()
        request.session = engine.SessionStore()
        await database_sync_to_async(login)(request, user, backend)

        # Save the session values.
        await database_sync_to_async(request.session.save)()

        # Create a cookie to represent the session.
        session_cookie = settings.SESSION_COOKIE_NAME
        cookies = SimpleCookie()
        cookies[session_cookie] = request.session.session_key
        cookie_data = {
            'max-age': None,
            'path': '/',
            'domain': settings.SESSION_COOKIE_DOMAIN,
            'secure': settings.SESSION_COOKIE_SECURE or None,
            'expires': None,
        }
        cookies[session_cookie].update(cookie_data)
        self.session = request.session
        self._session_cookie = bytes(
            cookies.output(header=''),
            encoding="utf-8",
        )

    @database_sync_to_async
    def login(self, **credentials):
        from django.contrib.auth import authenticate
        user = authenticate(**credentials)
        if user:
            self._login(user)

    async def force_login(self, user, backend=None):
        def get_backend():
            from django.contrib.auth import load_backend
            for backend_path in settings.AUTHENTICATION_BACKENDS:
                backend = load_backend(backend_path)
                if hasattr(backend, 'get_user'):
                    return backend_path

        if backend is None:
            backend = get_backend()
        user.backend = backend
        await self._login(user, backend)

    async def logout(self):
        """Log out the user by removing the cookies and session object."""
        from django.contrib.auth import get_user, logout

        request = HttpRequest()
        engine = import_module(settings.SESSION_ENGINE)
        if self.session:
            request.session = self.session
            request.user = await database_sync_to_async(get_user)(request)
        else:
            request.session = engine.SessionStore()
        await database_sync_to_async(logout)(request)
        self._session_cookie = b''

    async def queue_empty(self, timeout=0.1):
        if not self.output_queue.empty():
            return False
        if timeout <= 0.01:
            await asyncio.sleep(timeout)
            return self.output_queue.empty()
        await asyncio.sleep(0.01)
        if not self.output_queue.empty():
            return False
        await asyncio.sleep(timeout - 0.01)
        return self.output_queue.empty()

    async def queue_count(self, wait=0.1):
        await asyncio.sleep(wait)
        return self.output_queue.qsize()
