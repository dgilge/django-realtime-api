from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .utils import close_user_channels

User = get_user_model()


# TODO
# * Disconnect sockets where the session timed out
#   (SESSION_COOKIE_AGE / 15 or so)

# Authentication

@receiver(user_logged_in)
def close_sockets_after_login(sender, **kwargs):
    """
    Closes WebSockets of anonymous users (the user was anonymous before).
    """
    close_user_channels(AnonymousUser().pk or '')


@receiver(user_logged_out)
def close_sockets_after_logout(sender, **kwargs):
    close_user_channels(kwargs['user'].pk)


# User

@receiver(post_save, sender=User)
def close_sockets_after_user_update(sender, **kwargs):
    """
    Closes WebSockets when a user object changed.

    is_active or has_usable_password might have changed or
    the permissions might take custom fields into consideration.
    It is unlikely that there are open WebSockets when you update your
    user profile. Therefore we don't check which fields changed.
    """
    # The user has to login after the object is created and we are
    # also listening to the user_logged_in signal.
    if not kwargs['created']:
        close_user_channels(kwargs['instance'].pk)


@receiver(post_delete, sender=User)
def close_sockets_after_user_deletion(sender, **kwargs):
    close_user_channels(kwargs['instance'].pk)


# Permission, Group M2M changed

# See below


# Permission, Group

# Permissions are checked for each received message. Therefore changes to
# permissions and groups have (almost) immediate effect.
