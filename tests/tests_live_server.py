import pytest
from channels.testing.live import ChannelsLiveServerTestCase
from django.contrib.auth import get_user_model
try:
    from selenium.webdriver.firefox.webdriver import WebDriver
except ImportError:
    WebDriver = None

from . import models


pytestmark = pytest.mark.skipif(
    WebDriver is None,
    reason='Selenium not installed',
)


@pytest.mark.skip(reason='ChannelsLiveServerTestCase has currently a bug')
class SomeLiveTests(ChannelsLiveServerTestCase):

    serve_static = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.selenium = WebDriver()
        cls.selenium.implicitly_wait(5)

    @classmethod
    def tearDownClass(cls):
        cls.selenium.quit()
        super().tearDownClass()

    def get_user(self):
        user_model = get_user_model()
        try:
            # Necessary because transactions don't work as expected
            return user_model.objects.get(username='Rebecca')
        except user_model.DoesNotExist:
            return user_model.objects.create_user('Rebecca', password='pw')

    def login(self, user):
        cmd = 'window.open("{}/auth/login/","_blank");'
        self.selenium.execute_script(cmd.format(self.live_server_url))
        self.selenium.switch_to_window(self.selenium.window_handles[1])
        self.selenium.find_element_by_name('username').send_keys(user.username)
        self.selenium.find_element_by_name('password').send_keys('pw')
        self.selenium.find_element_by_name('submit').click()
        self.selenium.execute_script('window.close();')
        self.selenium.switch_to_window(self.selenium.window_handles[0])

    def logout(self):
        cmd = 'window.open("{}/auth/logout/","_blank");'
        self.selenium.execute_script(cmd.format(self.live_server_url))
        self.selenium.switch_to_window(self.selenium.window_handles[1])
        self.selenium.execute_script('window.close();')
        self.selenium.switch_to_window(self.selenium.window_handles[0])

    def test_session_authentication_create(self):
        # self.login(self.create_user())
        self.selenium.get(self.live_server_url + '/ws/')
        self.assertEqual(models.APIModel.objects.count(), 0)
        button_create = self.selenium.find_element_by_id('button-create')
        button_create.click()
        result = self.selenium.find_element_by_id('result')
        expected = {'status': 403, 'text': {
            'detail': 'You do not have permission to perform this action.',
        }}
        self.assertJSONEqual(result.get_attribute('innerHTML'), expected)
        self.assertEqual(models.APIModel.objects.count(), 0)

        # Log in
        self.login(self.get_user())
        button_create.click()
        self.assertEqual(models.APIModel.objects.count(), 1)
        self.assertJSONEqual(
            result.get_attribute('innerHTML'),
            {'status': 201, 'text': {'detail': 'creation successful'}},
        )

        # Log out
        self.logout()
        button_create.click()
        self.assertEqual(models.APIModel.objects.count(), 1)
        # result = self.selenium.find_element_by_id('result')
        expected = {'status': 403, 'text': {
            'detail': 'You do not have permission to perform this action.',
        }}
        self.assertJSONEqual(result.get_attribute('innerHTML'), expected)
        self.assertEqual(models.APIModel.objects.count(), 0)
