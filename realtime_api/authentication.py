class SessionAuthentication:
    """
    A dummy auth.
    """
    def authenticate(self, user):
        if user and user.is_active:
            return user
